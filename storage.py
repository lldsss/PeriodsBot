"""SQLite diary; atomic legacy migration and last-20-action undo."""
import calendar
import json
import os
import sqlite3
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from datetime import date, datetime, timedelta
from statistics import mean, median

# Fixed roles: an old MASHA_ID setting must not turn the test account into Masha.
MASHA = 1842996053
TEST_USER = 8651332701
WRITERS = (MASHA, TEST_USER)
current_user = ContextVar('diary_user', default=MASHA)
PARTNER = int(os.environ.get('PARTNER_ID', '1307205193'))


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, time TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS events_day ON events(day)')
            self.db.execute('CREATE TABLE IF NOT EXISTS periods (id INTEGER PRIMARY KEY AUTOINCREMENT, start TEXT NOT NULL, end TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, snapshot TEXT)')
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries'").fetchone():
                self.db.execute("INSERT INTO events(day,time,kind,value) SELECT day,'',kind,value FROM entries")
                self.db.execute('DROP TABLE entries')
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('schema_version','2')")

    def get_setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, str(value)))

    def sharing(self):
        return True

    def _snapshot(self, label):
        snapshot = {table: [dict(r) for r in self.db.execute(f'SELECT * FROM {table}')] for table in ('events', 'periods')}
        self.db.execute('INSERT INTO history(label,snapshot) VALUES (?,?)', (label, json.dumps(snapshot, ensure_ascii=False)))
        self.db.execute('DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 20)')

    def last_action(self):
        row = self.db.execute('SELECT id,label FROM history ORDER BY id DESC LIMIT 1').fetchone()
        return dict(row) if row else None

    def undo(self, expected_id):
        row = self.db.execute('SELECT * FROM history ORDER BY id DESC LIMIT 1').fetchone()
        if not row or row['id'] != expected_id:
            raise ValueError('Действие уже изменилось. Открой «Отменить последнее» ещё раз.')
        snapshot = json.loads(row['snapshot'])
        with self.db:
            self.db.execute('DELETE FROM events')
            self.db.execute('DELETE FROM periods')
            self.db.executemany('INSERT INTO events(id,day,time,kind,value) VALUES (:id,:day,:time,:kind,:value)', snapshot['events'])
            self.db.executemany('INSERT INTO periods(id,start,end) VALUES (:id,:start,:end)', snapshot['periods'])
            self.db.execute('DELETE FROM history WHERE id=?', (row['id'],))
        return row['label']

    def add(self, day, kind, value, time=''):
        date.fromisoformat(day)
        with self.db:
            self._snapshot(f'добавление «{kind}» за {day}')
            cur = self.db.execute('INSERT INTO events(day,time,kind,value) VALUES (?,?,?,?)', (day, time, kind, value))
        return cur.lastrowid

    def event(self, event_id):
        row = self.db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        return dict(row) if row else None

    def events(self, day):
        return [dict(r) for r in self.db.execute('SELECT * FROM events WHERE day=? ORDER BY time,id', (day,))]

    def edit(self, event_id, value, time=None):
        old = self.event(event_id)
        if not old:
            raise ValueError('Запись уже удалена.')
        with self.db:
            self._snapshot(f'изменение «{old["kind"]}» за {old["day"]}')
            self.db.execute('UPDATE events SET value=?,time=? WHERE id=?', (value, old['time'] if time is None else time, event_id))

    def delete_event(self, event_id):
        old = self.event(event_id)
        if not old:
            raise ValueError('Запись уже удалена.')
        with self.db:
            self._snapshot(f'удаление «{old["kind"]}» за {old["day"]}')
            self.db.execute('DELETE FROM events WHERE id=?', (event_id,))

    def clear(self):
        with self.db:
            self._snapshot('очистка дневника')
            self.db.execute('DELETE FROM events')
            self.db.execute('DELETE FROM periods')

    def periods(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM periods ORDER BY start')]

    def period(self, period_id):
        row = self.db.execute('SELECT * FROM periods WHERE id=?', (period_id,)).fetchone()
        return dict(row) if row else None

    def active_period(self):
        row = self.db.execute('SELECT * FROM periods WHERE end IS NULL').fetchone()
        return dict(row) if row else None

    def _check_period(self, start, end, exclude=None):
        date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
        if end and end < start:
            raise ValueError('Конец месячных не может быть раньше начала.')
        for p in self.periods():
            if p['id'] != exclude and start <= (p['end'] or '9999-12-31') and (end or '9999-12-31') >= p['start']:
                raise ValueError('Этот период пересекается с уже отмеченным. Исправь существующий период.')

    def start_period(self, day, end=None):
        self._check_period(day, end)
        with self.db:
            self._snapshot(f'начало месячных {day}')
            cur = self.db.execute('INSERT INTO periods(start,end) VALUES (?,?)', (day,end))
        return cur.lastrowid

    def update_period(self, period_id, start=None, end=None):
        old = self.period(period_id)
        if not old:
            raise ValueError('Период уже удалён.')
        start = start or old['start']
        end = end or old['end']
        self._check_period(start, end, period_id)
        with self.db:
            self._snapshot(f'изменение месячных с {old["start"]}')
            self.db.execute('UPDATE periods SET start=?,end=? WHERE id=?', (start, end, period_id))

    def delete_period(self, period_id):
        old = self.period(period_id)
        if not old:
            raise ValueError('Период уже удалён.')
        with self.db:
            self._snapshot(f'удаление месячных с {old["start"]}')
            self.db.execute('DELETE FROM periods WHERE id=?', (period_id,))

    def period_days(self, until):
        limit = date.fromisoformat(until)
        days = {r['day'] for r in self.db.execute("SELECT day FROM events WHERE kind='Месячные' AND value='Да' AND day<=?", (until,))}
        for p in self.periods():
            day = date.fromisoformat(p['start'])
            end = min(date.fromisoformat(p['end']) if p['end'] else limit, limit)
            while day <= end:
                days.add(day.isoformat())
                day += timedelta(days=1)
        return days

    def cycle_data(self, until):
        days = sorted(date.fromisoformat(d) for d in self.period_days(until))
        starts = [d for i, d in enumerate(days) if i == 0 or (d-days[i-1]).days > 10]
        starts = sorted(set(starts + [date.fromisoformat(p['start']) for p in self.periods() if p['start'] <= until]))
        # Explicit starts inside a legacy cluster replace its inferred start.
        cleaned = []
        for d in starts:
            if not cleaned or (d-cleaned[-1]).days > 10:
                cleaned.append(d)
        intervals = [(b-a).days for a, b in zip(cleaned, cleaned[1:])][-6:]
        durations = [(date.fromisoformat(p['end'])-date.fromisoformat(p['start'])).days+1 for p in self.periods() if p['end'] and p['end'] <= until]
        result = {'starts': cleaned, 'intervals': intervals, 'durations': durations, 'prediction': None}
        if len(intervals) >= 2:
            center = round(median(intervals))
            spread = max(2, round(max(abs(n-center) for n in intervals)))
            expected = cleaned[-1]+timedelta(days=center)
            result['prediction'] = (expected-timedelta(days=spread), expected+timedelta(days=spread))
        return result

    def forecast(self, until=None):
        until = until or date.today().isoformat()
        data = self.cycle_data(until)
        if not data['prediction']:
            return 'Для приблизительного прогноза нужно отметить минимум три цикла. Используй «Начались» и «Закончились».'
        low, high = data['prediction']
        text = f'По {len(data["intervals"])} интервалам: обычно около {round(median(data["intervals"]))} дн.\nОкно следующего начала: {low:%d.%m.%Y}–{high:%d.%m.%Y}.'
        if high < date.fromisoformat(until):
            text += '\nЭто окно уже прошло. Проверь, внесены ли последние месячные; прогноз автоматически вперёд не сдвигается.'
        text += '\nДанных пока мало.' if len(data['intervals']) < 4 else '\nОценка основана на последних циклах; разброс может меняться.'
        return text + '\nПрогноз приблизительный, не диагноз и не способ контрацепции.'

    def marks(self, month, until):
        date.fromisoformat(month+'-01')
        marks = {}
        for day in self.period_days(until):
            if day.startswith(month):
                marks.setdefault(int(day[-2:]), set()).add('М')
        for row in self.db.execute("SELECT day,kind,value FROM events WHERE day LIKE ? AND kind IN ('Секс','Близость')", (month+'-%',)):
            if row['value'].strip().casefold() not in ('', 'нет'):
                marks.setdefault(int(row['day'][-2:]), set()).add('С' if row['kind']=='Секс' else 'Б')
        return marks

    def stats(self, until, days=90):
        end = date.fromisoformat(until)
        start = (end-timedelta(days=days-1)).isoformat()
        events = [dict(r) for r in self.db.execute('SELECT * FROM events WHERE day BETWEEN ? AND ? ORDER BY day,time,id', (start, until))]
        symptoms, moods, pain = Counter(), Counter(), {}
        for e in events:
            if e['kind'] == 'Симптомы':
                for item in set(v.strip() for v in e['value'].split(',') if v.strip() and v.strip() != 'Нет симптомов'):
                    symptoms[item] += 1
            if e['kind'] == 'Настроение':
                moods[e['value']] += 1
            if e['kind'] == 'Боль' and (e['value'] in [str(n) for n in range(1,11)] or e['value'] == 'Нет боли'):
                pain.setdefault(e['day'], []).append(0 if e['value']=='Нет боли' else int(e['value']))
        return {'events': len(events), 'symptoms': symptoms.most_common(5), 'moods': moods.most_common(5),
                'pain': {d: mean(v) for d,v in pain.items()},
                'mood_history': [(e['day'],e['time'],e['value']) for e in events if e['kind']=='Настроение'][-7:], 'cycles': self.cycle_data(until), 'start': start, 'end': until}

    def delete_day(self, day):
        date.fromisoformat(day)
        with self.db:
            self._snapshot(f'удаление записей за {day}')
            self.db.execute('DELETE FROM events WHERE day=?', (day,))


class DiaryStores:
    """Route each update to its owner's database using task-local context.

    The partner reads Masha's diary. The test database never contains Masha's
    entries, settings, periods or undo snapshots, even when numeric IDs match.
    """
    def __init__(self, main_path, test_path=None):
        if PARTNER in WRITERS:
            raise ValueError('PARTNER_ID должен отличаться от ID Маши и тестового аккаунта.')
        if str(main_path)==':memory:':
            test_path=test_path or ':memory:'
        else:
            main_path=Path(main_path)
            test_path=Path(test_path) if test_path else main_path.with_name(f'{main_path.stem}.test-{TEST_USER}{main_path.suffix}')
            if main_path.resolve()==test_path.resolve() or (main_path.exists() and test_path.exists() and main_path.samefile(test_path)):
                raise ValueError('Основной и тестовый дневники должны использовать разные файлы базы.')
            main_path.parent.mkdir(parents=True,exist_ok=True)
            test_path.parent.mkdir(parents=True,exist_ok=True)
        self._stores={MASHA:Store(main_path)}
        try:
            self._stores[TEST_USER]=Store(test_path)
        except Exception:
            self._stores[MASHA].db.close()
            raise

    def for_user(self,user_id):
        if user_id not in (MASHA, TEST_USER, PARTNER):
            raise ValueError('Доступ закрыт.')
        return self._stores[TEST_USER if user_id==TEST_USER else MASHA]

    def __getattr__(self,name):
        return getattr(self.for_user(current_user.get()),name)

    def close(self):
        for diary in self._stores.values():
            diary.db.close()
