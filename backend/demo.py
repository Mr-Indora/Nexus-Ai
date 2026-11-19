"""Demo data (no Google account needed). Dates are relative to 'now' so it never goes stale."""
from datetime import datetime, timedelta

from timeutil import TZ, local_now, local_to_utc_naive, utcnow


def _d(offset_days: int, fmt: str = "%d %B %Y") -> str:
    return (local_now() + timedelta(days=offset_days)).strftime(fmt)


def _at(offset_days: int, hour: int, minute: int = 0) -> datetime:
    d = (local_now() + timedelta(days=offset_days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local_to_utc_naive(d)


def emails() -> list[dict]:
    n = utcnow()
    raw = [
        ("d1", "Prof. R. Sharma <rsharma@gecajmer.ac.in>", "DBMS Assignment 3 — submission",
         f"Dear students, please submit the Normalization assignment (chapters 4-6) as a PDF on the course portal by tomorrow 6:00 PM. Late submissions will not be accepted.", 3),
        ("d2", "Training & Placement Cell <tnp@gecajmer.ac.in>", "TCS Campus Drive — registration closing",
         f"The TCS on-campus drive registration closes on {_d(2)} at 11:59 PM. Students must register and upload an updated resume. This is the last date, no extension.", 6),
        ("d3", "Exam Section <exam@btu.ac.in>", "Semester 7 examination form",
         f"Fill the examination form before {_d(5, '%d/%m/%Y')} to avoid the late fee. Please pay the fee online and upload the receipt.", 20),
        ("d4", "HR Team <careers@startupco.io>", "Interview scheduled — AI/ML Intern",
         f"Hi Hemant, your technical interview round is scheduled on {_d(3)} at 4:30 PM. Please confirm your availability by replying to this email.", 10),
        ("d5", "IIT Madras BS Degree <support@study.iitm.ac.in>", "Quiz 2 reminder",
         f"Reminder: Quiz 2 will be held on {_d(6)} at 10:00 AM. Please complete the pre-quiz mock test before that.", 30),
        ("d6", "Scholarship Cell <scholarship@rajasthan.gov.in>", "Scholarship renewal documents",
         f"Kindly upload the income certificate and bonafide certificate for scholarship renewal by {_d(9)}.", 40),
        ("d7", "Medium Daily Digest <noreply@medium.com>", "Top stories for you",
         "Here are today's top stories in AI. Read more... Unsubscribe from this newsletter anytime.", 15),
        ("d8", "Swiggy <noreply@swiggy.in>", "Your receipt from Swiggy", "Thanks for ordering! Your receipt is attached. 20% off on your next order, promo code SAVE20.", 50),
        ("d9", "Dr. Meena <meena@gecajmer.ac.in>", "Guest lecture on Reliability Engineering",
         f"A guest lecture on Reliability Engineering is scheduled on {_d(4)} at 11:00 AM in Seminar Hall. Attendance is optional but recommended.", 70),
    ]
    return [
        {"gmail_id": f"demo-{i}", "sender": s, "subject": subj, "snippet": body[:120], "body": body,
         "received_at": n - timedelta(hours=h)}
        for i, s, subj, body, h in raw
    ]


def events() -> list[dict]:
    meet = "https://meet.google.com/abc-defg-hij"
    rows = [
        ("e1", "Study — DBMS", 0, 9, 0, 10, 30, "", ""),
        ("e2", "Team Sync — Nexus AI", 0, 11, 0, 11, 45, "", meet),
        ("e3", "Placement Preparation", 0, 14, 0, 15, 30, "Library", ""),
        ("e4", "Mentor call — project review", 0, 18, 0, 18, 30, "", "https://meet.google.com/xyz-mnop-qrs"),
        ("e5", "Technical Interview — AI/ML Intern", 3, 16, 30, 17, 30, "", "https://meet.google.com/int-erview-123"),
        ("e6", "Guest Lecture — Reliability Engineering", 4, 11, 0, 12, 0, "Seminar Hall", ""),
        ("e7", "IITM Quiz 2", 6, 10, 0, 11, 0, "", ""),
        ("e8", "Hostel fee payment window", 8, 0, 0, 0, 0, "", ""),
    ]
    out = []
    for gid, title, off, h1, m1, h2, m2, loc, link in rows:
        all_day = gid == "e8"
        start = _at(off, h1, m1)
        end = _at(off, h2, m2) if not all_day else start + timedelta(days=1)
        out.append({"google_id": f"demo-{gid}", "title": title, "start": start, "end": end, "all_day": all_day,
                    "location": loc, "meet_link": link, "description": ""})
    return out
