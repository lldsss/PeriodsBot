import sqlite3
import tempfile
import unittest
from pathlib import Path
from storage import Store


class StoreTests(unittest.TestCase):
    def setUp(self): self.s=Store(':memory:')
    def tearDown(self): self.s.db.close()

    def test_legacy_migration_once_and_sharing_always(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'diary.sqlite3'
            db=sqlite3.connect(path)
            db.executescript("CREATE TABLE entries(day TEXT,kind TEXT,value TEXT,PRIMARY KEY(day,kind)); CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT); INSERT INTO settings VALUES('sharing','no'); INSERT INTO entries VALUES('2026-10-01','Симптомы','Спазмы'); INSERT INTO entries VALUES('2026-10-01','Месячные','Да');")
            db.close()
            for _ in range(2):
                migrated=Store(path)
                self.assertEqual(len(migrated.events('2026-10-01')),2)
                self.assertTrue(migrated.sharing())
                self.assertEqual(migrated.marks('2026-10','2026-10-14')[1],{'М'})
                migrated.db.close()

    def test_multiple_events_edit_delete_and_undo(self):
        first=self.s.add('2026-10-01','Боль','3','09:00')
        second=self.s.add('2026-10-01','Боль','8','21:00')
        self.assertEqual(len(self.s.events('2026-10-01')),2)
        self.s.edit(first,'5','10:00')
        action=self.s.last_action()
        self.s.undo(action['id'])
        self.assertEqual(self.s.event(first)['value'],'3')
        self.assertEqual(self.s.event(first)['time'],'09:00')
        with self.assertRaises(ValueError): self.s.undo(action['id'])
        self.s.delete_event(second)
        self.assertIsNone(self.s.event(second))
        self.s.undo(self.s.last_action()['id'])
        self.assertEqual(self.s.event(second)['value'],'8')

    def test_period_fill_end_and_correct(self):
        pid=self.s.start_period('2026-10-01')
        self.assertIn('2026-10-14',self.s.period_days('2026-10-14'))
        self.s.update_period(pid,end='2026-10-05')
        self.assertEqual(len(self.s.period_days('2026-10-14')),5)
        self.s.update_period(pid,start='2026-10-02')
        self.assertEqual(len(self.s.period_days('2026-10-14')),4)
        with self.assertRaises(ValueError): self.s.update_period(pid,end='2026-10-01')
        with self.assertRaises(ValueError): self.s.start_period('2026-10-03')
        self.s.delete_period(pid)
        self.s.undo(self.s.last_action()['id'])
        self.assertEqual(self.s.period(pid)['end'],'2026-10-05')

    def test_clear_and_day_delete_undo(self):
        self.s.add('2026-10-01','Заметка','one')
        self.s.add('2026-10-02','Заметка','two')
        pid=self.s.start_period('2026-10-01')
        self.s.delete_day('2026-10-01')
        self.assertEqual(self.s.events('2026-10-01'),[])
        self.assertIsNotNone(self.s.period(pid))
        self.s.undo(self.s.last_action()['id'])
        self.s.clear()
        self.assertEqual(self.s.periods(),[])
        self.s.undo(self.s.last_action()['id'])
        self.assertEqual(len(self.s.events('2026-10-01')),1)
        self.assertIsNotNone(self.s.period(pid))

    def test_marks_multiple_positive_negative_events(self):
        self.s.add('2026-10-01','Секс','Да','09:00')
        self.s.add('2026-10-01','Секс','Нет','21:00')
        self.s.add('2026-10-01','Близость','Обнимались')
        self.assertEqual(self.s.marks('2026-10','2026-10-14')[1],{'С','Б'})

    def test_forecast_requires_three_cycles_and_reports_range(self):
        self.assertIn('минимум три',self.s.forecast('2026-10-14'))
        for start,end in [('2026-08-01','2026-08-05'),('2026-08-29','2026-09-02'),('2026-09-26','2026-09-30')]:
            pid=self.s.start_period(start)
            self.s.update_period(pid,end=end)
        data=self.s.cycle_data('2026-10-14')
        self.assertEqual(data['intervals'],[28,28])
        self.assertEqual(data['durations'],[5,5,5])
        self.assertEqual(str(data['prediction'][0]),'2026-10-22')
        self.assertEqual(str(data['prediction'][1]),'2026-10-26')
        self.assertIn('окно уже прошло',self.s.forecast('2026-11-01'))

    def test_stats_count_symptoms_and_average_pain_without_filling_gaps(self):
        self.s.add('2026-10-01','Симптомы','Спазмы, Усталость')
        self.s.add('2026-10-01','Боль','2','09:00')
        self.s.add('2026-10-01','Боль','8','21:00')
        self.s.add('2026-10-03','Боль','Нет боли')
        self.s.add('2026-01-01','Боль','10')
        stats=self.s.stats('2026-10-14')
        self.assertEqual(stats['pain'],{'2026-10-01':5,'2026-10-03':0})
        self.assertIn(('Спазмы',1),stats['symptoms'])

    def test_undo_limit_and_persistence(self):
        for i in range(25): self.s.add('2026-10-01','Заметка',str(i))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM history').fetchone()[0],20)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.db'
            saved=Store(path)
            saved.add('2026-10-01','Заметка','test')
            saved.set_setting('reminder_time','20:30')
            saved.db.close()
            reopened=Store(path)
            self.assertEqual(reopened.get_setting('reminder_time'),'20:30')
            reopened.undo(reopened.last_action()['id'])
            self.assertEqual(reopened.events('2026-10-01'),[])
            reopened.db.close()
