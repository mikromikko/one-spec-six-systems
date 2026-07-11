# Specification: invoice approval service

Build an internal web service for Nordvik Components Oy, a 400-person manufacturing company in the EU. The service handles approval of incoming supplier invoices.

## Requirements

All requirements are MUST.

1. Users sign in with email and password. There are two roles: submitter and approver. A user has exactly one role.
2. A submitter can create an invoice with: amount in euros, counterparty name, counterparty contact email, due date, and a PDF attachment.
3. An invoice moves through states: submitted, approved, rejected. Only approvers can approve or reject.
4. An approver can never approve or reject an invoice they submitted themselves.
5. An invoice with an amount over 10,000 euros becomes approved only after approvals from two distinct approvers. At or under 10,000 euros, one approval is enough.
6. Every state change is recorded in an append-only audit log entry: who, what changed (state before and after), and when. The application provides no way to edit or delete audit log entries.
7. The counterparty contact email is personal data: the service provides a way to erase it on request, and every read access to it is recorded in the audit log.
8. Approvers see a list of invoices pending their approval, filterable by counterparty name and by amount.
9. On every invoice state change, the service sends an email notification to the submitter. A stub that writes the email to a log or file is acceptable; no real SMTP is required.
10. The project includes automated tests and a README with the exact steps to run the system and the tests locally.

## Constraints

- Choose any language, framework, and storage.
- The system must run locally following only the README steps.
- Do everything in the current directory. Work until the system is complete; do not ask questions.
