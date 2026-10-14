import calendar
import sqlite3
from datetime import date, timedelta

# MASHA = 1842996053
MASHA = 8651332701
PARTNER = 1307205193


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS entries (day TEXT, kind TEXT, value TEXT, PRIMARY KEY(day, kind))')
        self.db.commit()

    def sharing(self):
        row = self.db.execute("SELECT value FROM settings WHERE key='sharing'").fetchone()
        return bool(row and row[0] == 'yes')

    def set_sharing(self, enabled):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('sharing', ?)", ('yes' if enabled else 'no',))
        self.db.commit()

    def put(self, day, kind, value):
        date.fromisoformat(day)
        self.db.execute('INSERT OR REPLACE INTO entries VALUES (?, ?, ?)', (day, kind, value))
        self.db.commit()

    def rows(self, day):
        return self.db.execute('SELECT kind, value FROM entries WHERE day=? ORDER BY kind', (day,)).fetchall()

    def delete(self, day):
        self.db.execute('DELETE FROM entries WHERE day=?', (day,))
        self.db.commit()

    def clear(self):
        self.db.execute('DELETE FROM entries')
        self.db.commit()

    def calendar(self, month):
        first = date.fromisoformat(month + '-01')
        records = self.db.execute('SELECT day, kind, value FROM entries WHERE day LIKE ?', (month + '-%',)).fetchall()
        marks = {}
        for day, kind, value in records:
            if (kind == 'Месячные' and value == 'Да') or (kind in ('Близость', 'Секс') and value.strip().casefold() not in ('', 'нет')):
                marks.setdefault(int(day[-2:]), set()).add({'Месячные': 'М', 'Близость': 'Б', 'Секс': 'С'}[kind])
        lines = [month, 'Пн     Вт     Ср     Чт     Пт     Сб     Вс']
        for week in calendar.monthcalendar(first.year, first.month):
            lines.append(' '.join((str(d) + ''.join(sorted(marks.get(d, ()))) if d else '').ljust(6) for d in week))
        return '\n'.join(lines) + '\nМ — месячные · Б — близость · С — секс'

    def forecast(self):
        days = [date.fromisoformat(r[0]) for r in self.db.execute("SELECT day FROM entries WHERE kind='Месячные' AND value='Да' ORDER BY day")]
        starts = [d for i, d in enumerate(days) if i == 0 or (d - days[i-1]).days > 10]
        intervals = [(b-a).days for a, b in zip(starts, starts[1:])]
        valid = [n for n in intervals[-6:] if 15 <= n <= 60]
        if len(valid) < 2:
            return 'Для приблизительного прогноза нужно отметить минимум три цикла. Отмечайте каждый день месячных.'
        average = round(sum(valid) / len(valid))
        predicted = starts[-1] + timedelta(days=average)
        return f'Средняя длина отмеченных циклов: {average} дн. Ориентировочное начало следующего: {predicted:%d.%m.%Y}. Это оценка по дневнику, не диагноз и не способ контрацепции.'
