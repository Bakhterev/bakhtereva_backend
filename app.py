import os
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from caldav import DAVClient
from icalendar import Calendar, Event
from datetime import datetime, timedelta, timezone
import uuid
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import pytz

load_dotenv()

app = Flask(__name__)
CORS(app)

# --- КОНФИГУРАЦИЯ ---
YANDEX_LOGIN = os.getenv('YANDEX_LOGIN')
YANDEX_APP_PASSWORD = os.getenv('YANDEX_APP_PASSWORD')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'events-38315911')
CALDAV_URL = "https://caldav.yandex.ru"

TZ = pytz.timezone('Asia/Yekaterinburg')
UTC = timezone.utc

def get_calendar():
    try:
        client = DAVClient(
            url=CALDAV_URL,
            username=YANDEX_LOGIN,
            password=YANDEX_APP_PASSWORD
        )
        principal = client.principal()
        calendars = principal.calendars()
        for cal in calendars:
            if CALENDAR_ID in cal.id:
                return cal, client
        if calendars:
            print(f"⚠️ Календарь '{CALENDAR_ID}' не найден, использую первый: {calendars[0].id}")
            return calendars[0], client
        return None, None
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return None, None

def fetch_event_data(event_url, login, password):
    session = requests.Session()
    session.auth = HTTPBasicAuth(login, password)
    try:
        response = session.get(event_url)
        if response.status_code == 200:
            return response.text
        else:
            print(f"⚠️ Ошибка загрузки события {event_url}: {response.status_code}")
            return None
    except Exception as e:
        print(f"⚠️ Исключение при загрузке: {e}")
        return None

def parse_ical_data(ical_str):
    if not ical_str:
        return []
    if '<C:calendar-data>' in ical_str:
        match = re.search(r'<C:calendar-data>(.*?)</C:calendar-data>', ical_str, re.DOTALL)
        if match:
            ical_str = match.group(1).strip()
    try:
        cal = Calendar.from_ical(ical_str)
        events = []
        for component in cal.walk():
            if component.name == "VEVENT":
                summary = component.get('summary', 'Без названия')
                dtstart = component.get('dtstart').dt
                dtend = component.get('dtend').dt
                if dtstart.tzinfo is None:
                    dtstart = pytz.UTC.localize(dtstart)
                else:
                    dtstart = dtstart.astimezone(pytz.UTC)
                if dtend.tzinfo is None:
                    dtend = pytz.UTC.localize(dtend)
                else:
                    dtend = dtend.astimezone(pytz.UTC)
                dtstart_ekb = dtstart.astimezone(TZ)
                dtend_ekb = dtend.astimezone(TZ)
                events.append({
                    'summary': str(summary),
                    'start': dtstart_ekb,
                    'end': dtend_ekb
                })
        return events
    except Exception as e:
        print(f"⚠️ Ошибка парсинга iCalendar: {e}")
        return []

@app.route('/slots', methods=['GET'])
def get_free_slots():
    date_str = request.args.get('date')
    consultation_type = request.args.get('consultation_type', 'online')
    if not date_str:
        return jsonify({"error": "Дата не указана"}), 400
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Неверный формат даты"}), 400

    today = datetime.now(TZ).date()
    if date < today:
        return jsonify({"error": "Нельзя выбрать прошедшую дату"}), 400

    weekday = date.weekday()  # 0-пн, 6-вс

    # Генерируем слоты в зависимости от типа и дня
    slots = []
    if consultation_type == 'online':
        if weekday == 6:  # воскресенье
            slots = ['18:00', '19:00']
        else:
            slots = []
    elif consultation_type == 'offline':
        if weekday == 0:  # понедельник
            slots = ['17:30', '18:30']
        else:
            slots = []
    else:
        slots = []

    # Если сегодня, убираем прошедшие слоты
    if date == today:
        now = datetime.now(TZ)
        slots_filtered = []
        for slot in slots:
            try:
                slot_time = datetime.strptime(slot, '%H:%M').time()
                slot_dt = datetime.combine(date, slot_time, tzinfo=TZ)
                if slot_dt > now:
                    slots_filtered.append(slot)
            except:
                continue
        slots = slots_filtered

    # Проверка занятости из календаря
    calendar_obj, client = get_calendar()
    if not calendar_obj:
        return jsonify({"error": "Календарь не найден"}), 500

    start_dt_utc = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=UTC)
    end_dt_utc = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=UTC)

    try:
        events = calendar_obj.date_search(start=start_dt_utc, end=end_dt_utc, expand=True)
    except Exception as e:
        print(f"Ошибка поиска: {e}")
        return jsonify({"error": f"Ошибка получения событий: {str(e)}"}), 500

    busy = []
    login = YANDEX_LOGIN
    password = YANDEX_APP_PASSWORD

    for event_item in events:
        event_url = None
        if hasattr(event_item, 'url'):
            event_url = event_item.url
        elif isinstance(event_item, str) and event_item.startswith('http'):
            event_url = event_item
        if not event_url:
            continue
        ical_data = fetch_event_data(event_url, login, password)
        if not ical_data:
            continue
        parsed_events = parse_ical_data(ical_data)
        for ev in parsed_events:
            busy.append((ev['start'], ev['end']))

    free_slots = []
    for slot in slots:
        slot_time = datetime.strptime(slot, '%H:%M').time()
        slot_start = datetime(date.year, date.month, date.day, slot_time.hour, slot_time.minute, tzinfo=TZ)
        duration_minutes = 40 if consultation_type == 'online' else 60
        slot_end = slot_start + timedelta(minutes=duration_minutes)
        is_free = True
        for busy_start, busy_end in busy:
            if not (slot_end <= busy_start or slot_start >= busy_end):
                is_free = False
                break
        if is_free:
            free_slots.append(slot)

    return jsonify(free_slots)

@app.route('/book', methods=['POST'])
def book_appointment():
    data = request.json
    patient_name = data.get('name')
    patient_phone = data.get('phone')
    patient_email = data.get('email')
    start_time_str = data.get('start_time')
    consultation_type = data.get('consultation_type', 'online')

    if not patient_name or not patient_phone or not start_time_str:
        return jsonify({"error": "Не хватает данных"}), 400

    try:
        start_time = datetime.fromisoformat(start_time_str).astimezone(UTC)
        duration_minutes = 40 if consultation_type == 'online' else 60
        end_time = start_time + timedelta(minutes=duration_minutes)

        calendar_obj, client = get_calendar()
        if not calendar_obj:
            return jsonify({"error": "Календарь не найден"}), 500

        date = start_time.date()
        start_dt_utc = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=UTC)
        end_dt_utc = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=UTC)
        events = calendar_obj.date_search(start=start_dt_utc, end=end_dt_utc, expand=True)
        login = YANDEX_LOGIN
        password = YANDEX_APP_PASSWORD

        for event_item in events:
            event_url = None
            if hasattr(event_item, 'url'):
                event_url = event_item.url
            elif isinstance(event_item, str) and event_item.startswith('http'):
                event_url = event_item
            if not event_url:
                continue
            ical_data = fetch_event_data(event_url, login, password)
            if not ical_data:
                continue
            parsed_events = parse_ical_data(ical_data)
            for ev in parsed_events:
                busy_start = ev['start']
                busy_end = ev['end']
                if not (end_time.astimezone(TZ) <= busy_start or start_time.astimezone(TZ) >= busy_end):
                    return jsonify({"error": "Это время уже занято"}), 409

        if consultation_type == 'online':
            location = 'Онлайн-консультация (Zoom)'
            description = (
                f'Телефон: {patient_phone}\n'
                f'Email: {patient_email or "Не указан"}\n\n'
                'Ссылка на встречу будет отправлена отдельно.'
            )
        else:
            location = 'г. Екатеринбург, ул. Примерная, д. 1 (очно)'
            description = f'Телефон: {patient_phone}\nEmail: {patient_email or "Не указан"}'

        cal = Calendar()
        cal.add('prodid', '-//My Calendar//')
        cal.add('version', '2.0')

        event = Event()
        event.add('uid', str(uuid.uuid4()))
        event.add('dtstamp', datetime.now(UTC))
        event.add('dtstart', start_time.astimezone(UTC))
        event.add('dtend', end_time.astimezone(UTC))
        event.add('summary', f'Консультация ({consultation_type}): {patient_name}')
        event.add('description', description)
        event.add('location', location)

        cal.add_component(event)
        ics_data = cal.to_ical().decode('utf-8')

        calendar_obj.save_event(ics_data)

        return jsonify({"success": True, "message": "Запись успешно создана!"}), 200

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)