"""Shared keyword classifier (used by rule-based extractor + calendar events)."""

CATEGORY_KEYWORDS = {
    "Career": ["placement", "internship", "interview", "job", "offer letter", "offer", "resume", "hiring",
               "recruit", "campus drive", "drive", "career", "referral", "hackathon", "application status"],
    "Academic": ["assignment", "exam", "lecture", "project", "syllabus", "quiz", "lab", "semester",
                 "submission", "course", "class", "viva", "tutorial", "seminar", "coursera", "iit madras",
                 "thesis", "research", "study", "professor"],
    "Administrative": ["fee", "form", "registration", "scholarship", "hostel", "library", "certificate",
                       "notice", "payment", "admission", "invoice", "renewal", "kyc", "document"],
}


def classify_category(text: str) -> str:
    t = text.lower()
    best, best_hits = "Informational", 0
    for cat, words in CATEGORY_KEYWORDS.items():
        hits = sum(1 for w in words if w in t)
        if hits > best_hits:
            best, best_hits = cat, hits
    return best
