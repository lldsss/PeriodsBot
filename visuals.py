"""Local PNG rendering; no diary data is sent to external image services."""
import calendar
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

MONTHS = ('Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь')
FONT = Path(__file__).parent / 'assets' / 'DejaVuSans.ttf'
INK, PINK, BG = '#663c50', '#e983a5', '#fff4f7'


def font(size):
    return ImageFont.truetype(str(FONT), size)


def png(image):
    stream = BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def pig(draw, x, y, r=40):
    draw.polygon([(x-r,y-r//2),(x-r,y-r*1.3),(x-r//3,y-r)], fill='#f2a4ba')
    draw.polygon([(x+r,y-r//2),(x+r,y-r*1.3),(x+r//3,y-r)], fill='#f2a4ba')
    draw.ellipse((x-r,y-r,x+r,y+r), fill='#ffc4d4', outline=PINK, width=2)
    for dx in (-r//3,r//3):
        draw.ellipse((x+dx-3,y-12,x+dx+3,y-6), fill=INK)
    draw.ellipse((x-r//2,y,x+r//2,y+r//2), fill='#ef96b0')
    for dx in (-r//5,r//5):
        draw.ellipse((x+dx-2,y+r//5-3,x+dx+2,y+r//5+3), fill=INK)


def calendar_image(store, month, today):
    first = date.fromisoformat(month+'-01')
    weeks = calendar.monthcalendar(first.year, first.month)
    image = Image.new('RGB', (1120, 330+len(weeks)*110), BG)
    draw = ImageDraw.Draw(image)
    draw.text((48,30), 'Машин хрю-дневничок', font=font(38), fill=INK)
    draw.text((48,86), f'{MONTHS[first.month-1]} {first.year}', font=font(28), fill=PINK)
    pig(draw, 1030, 75)
    marks = store.marks(month, today)
    prediction = store.cycle_data(today)['prediction']
    for i,label in enumerate(('ПН','ВТ','СР','ЧТ','ПТ','СБ','ВС')):
        draw.text((55+i*147,145), label, font=font(21), fill=INK)
    for row,week in enumerate(weeks):
        for column,day in enumerate(week):
            if not day:
                continue
            x,y = 40+column*147, 183+row*110
            stamp = first.replace(day=day)
            tags = marks.get(day,set())
            forecast = prediction and prediction[0] <= stamp <= prediction[1] and stamp.isoformat() > today and 'М' not in tags
            color = '#ffd4e0' if 'М' in tags else '#eee4fa' if forecast else '#ffffff'
            draw.rounded_rectangle((x,y,x+135,y+99), radius=17, fill=color, outline=PINK if stamp.isoformat()==today else '#efdde5', width=3 if stamp.isoformat()==today else 1)
            draw.text((x+12,y+8), str(day), font=font(25), fill=INK)
            draw.text((x+12,y+54), ' '.join(t for t in ('М','Б','С') if t in tags) or ('~' if forecast else ''), font=font(24), fill=INK)
    bottom = 193+len(weeks)*110
    draw.text((40,bottom), 'М — месячные     Б — близость     С — секс', font=font(23), fill=INK)
    draw.text((40,bottom+36), 'Лиловый ~ — примерное окно начала, не отмеченные месячные', font=font(20), fill=INK)
    draw.text((40,bottom+69), 'Нежная забота о себе, день за днём', font=font(19), fill=PINK)
    return png(image)


def pain_chart(stats):
    image = Image.new('RGB', (1120, 530), BG)
    d = ImageDraw.Draw(image)
    d.text((45,25), 'Боль за последние 7 дней', font=font(36), fill=INK)
    d.text((45,78), 'Средняя оценка за день · пустые дни не означают отсутствие боли', font=font(20), fill=INK)
    pig(d,1030,63,30)
    start, end = date.fromisoformat(stats['start']), date.fromisoformat(stats['end'])
    span = max(1,(end-start).days)
    for n in range(0,11,2):
        y=420-n*28
        d.line((70,y,1060,y), fill='#ead9e1', width=1)
        d.text((30,y-12), str(n), font=font(18), fill=INK)
    previous = None
    for day,value in sorted(stats['pain'].items()):
        stamp=date.fromisoformat(day)
        x=70+(stamp-start).days/span*990
        y=420-value*28
        if previous and (stamp-previous[0]).days == 1:
            d.line((previous[1],previous[2],x,y), fill=PINK, width=3)
        d.ellipse((x-5,y-5,x+5,y+5), fill=PINK)
        previous=(stamp,x,y)
    for offset in (range(span+1) if span<=6 else (0,span//2,span)):
        x=70+offset/span*990
        d.text((min(x,970),447), (start+timedelta(days=offset)).strftime('%d.%m'), font=font(18), fill=INK)
    if not stats['pain']:
        d.text((300,260), 'Пока нет оценок боли', font=font(28), fill=INK)
    return png(image)
