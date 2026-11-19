"""Synthetic, non-sensitive extraction scenarios."""
from datetime import datetime

REFERENCE = datetime(2026, 11, 10, 12, 0)


def _case(name, subject, body, actionable, deadline=False):
    return {
        "name": name,
        "email": {
            "subject": subject,
            "body": body,
            "sender": "sender@example.test",
            "received_at": REFERENCE,
        },
        "expected": {"actionable": actionable, "deadline": deadline},
    }


SCENARIOS = [
    _case("factual_notice", "Library hours", "The library is open from 9 AM to 6 PM.", False),
    _case("simple_request", "Please review", "Please review the attached draft and reply.", True),
    _case("explicit_commitment", "Team commitment", "I will submit the report by 2027-01-12.", True, True),
    _case("decision_confirmed", "Decision confirmed", "We agreed to use the second design.", False),
    _case("hard_deadline", "Assignment deadline", "Submit the assignment by 2027-01-15.", True, True),
    _case("meeting_invitation", "Meeting invitation", "Please attend the meeting on 2027-01-20 at 10 AM.", True, True),
    _case("reschedule_request", "Reschedule meeting", "Please confirm whether 2027-01-21 works instead.", True),
    _case("pending_response", "Response needed", "Please reply by 2027-01-22.", True, True),
    _case("thread_first", "Project update", "The draft is ready for review.", False),
    _case("thread_second", "Re: Project update", "I will review and respond tomorrow.", True, True),
    _case("conflicting_statement", "Schedule change", "The deadline is 2027-02-01, not 2027-01-25.", True, True),
    _case("ambiguous_language", "Possible date", "We may submit sometime next week.", False),
    _case("missing_deadline", "Please apply", "Please apply for the program when convenient.", True),
    _case("cancelled_commitment", "Cancelled submission", "The submission is cancelled; no action is required.", False),
    _case("updated_commitment", "Updated deadline", "The new deadline is 2027-02-10. Ignore the old date.", True, True),
    _case("duplicate_message", "Reminder", "Please upload the form by 2027-02-12.", True, True),
    _case("newsletter", "Weekly newsletter", "Read our newsletter. Unsubscribe anytime.", False),
    _case("spam_like", "You won a prize", "Claim your prize now. Sale ends 2027-03-01.", False),
    _case("hindi_request", "असाइनमेंट जमा करें", "कृपया असाइनमेंट 2027-03-05 तक जमा करें।", True, True),
    _case("hinglish_request", "Form submit karna hai", "Please form 2027-03-06 se pehle submit karo.", True, True),
    _case("short_reply", "Re: Thanks", "Thanks, received.", False),
    _case("professional_long", "Quarterly review", "Please prepare the review notes and send them by 2027-03-10. Include risks and next steps.", True, True),
    _case("informational_event", "Webinar", "The webinar is scheduled for 2027-03-11.", False),
    _case("action_event", "Interview scheduled", "Your interview is scheduled for 2027-03-12. Please attend.", True, True),
    _case("payment_request", "Invoice payment", "Please pay the invoice before 2027-03-13.", True, True),
    _case("confirmation_request", "Confirm attendance", "Please confirm attendance for the event.", True),
    _case("application", "Application open", "Applications are open until 2027-03-15. Apply now.", True, True),
    _case("reminder", "Reminder", "Reminder: complete the registration by 2027-03-16.", True, True),
    _case("no_action_fact", "Policy update", "The policy changed on 2027-03-17.", False),
    _case("reply_without_date", "Question", "Can you reply with your availability?", True),
    _case("deadline_only", "Deadline notice", "The deadline is 2027-03-18.", True, True),
    _case("cancelled_event", "Event cancelled", "The meeting on 2027-03-19 is cancelled.", False),
    _case("future_commitment", "I will do it", "I will complete the migration next week.", True),
    _case("request_no_due", "Feedback requested", "Kindly share feedback on the proposal.", True),
    _case("receipt", "Your receipt", "Your receipt is attached. Thank you for your purchase.", False),
    _case("marketing", "Special offer", "Save 20% today. Unsubscribe from these promotions.", False),
    _case("review_deadline", "Review due", "Review the document and return comments by 2027-03-20.", True, True),
    _case("decision_not_request", "Final decision", "The committee decided to proceed with option A.", False),
    _case("pending_confirmation", "Awaiting confirmation", "We are awaiting your response on the proposed time.", True),
    _case("long_factual", "System maintenance", "Maintenance will occur on 2027-03-22. No action is needed from you.", False),
    _case("explicit_cancel", "No response needed", "Please disregard the earlier request; it has been cancelled.", False),
]
