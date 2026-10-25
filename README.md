# VoiceDesk

VoiceDesk is a demo complaint-management application for customers who want to
submit a spoken complaint in a supported language and receive a ticket number.
It combines a React customer interface with a Django backend and an AudicLabs
speech-to-text integration.

> **Assessment/demo status:** VoiceDesk includes complaint submission, ticket
> lookup, and a basic agent queue. It is not a production-ready support system.
> In particular, the agent workflow is unauthenticated and no real translation
> provider is included.

## Architecture

- **Frontend:** React 19 and Vite, under `frontend/`. The customer UI records
  audio in the browser, optionally attaches a photo, submits complaints, looks
  up tickets, and shows a basic agent queue.
- **Backend:** Django 5.2 and Django REST Framework, under `backend/`.
  Complaint handling, AudicLabs transcription, optional translation adapter,
  deterministic suggestions, duplicate detection, and the API routes are in
  `backend/complaints/`.
- **Database:** SQLite at `backend/db.sqlite3` by default.
- **Uploaded files:** Django local media storage under `backend/media/` by
  default. It is not cloud/object storage.

Complaint processing currently follows this path:

1. Django validates the submitted phone-number format and uploaded file types,
   then saves the complaint and its audio/photo.
2. If `AUDICLABS_API_KEY` is configured, the backend sends the audio to the
   AudicLabs speech-to-text API.
3. A successful transcript is stored as `original_transcript`. The built-in
   category, priority, and summary logic produces **unreviewed suggestions**.
4. A configured translation adapter may translate the transcript into English.
   Without one, the complaint is retained and its processing status indicates
   that translation is unavailable.
5. The backend checks for an earlier possible duplicate from the same phone
   number within seven days, based on matching normalized text.

## Implemented features

- Customer complaint submission with browser audio recording and optional
  photo upload.
- AudicLabs speech-to-text integration, including request-ID logging,
  timeouts, and a single retry when a supported `Retry-After` response is
  received.
- Ticket UUID generation, saved transcript, ticket status, and processing
  status.
- Read-only ticket lookup by UUID.
- Newest-first complaint queue with status, category, priority, and language
  filters, plus status updates.
- Deterministic category, priority, and summary suggestions. These are
  identified as suggestions and are not human-confirmed decisions.
- Possible-duplicate detection that preserves both tickets.
- Focused Django tests that mock external speech-to-text and translation
  behavior.

## Windows setup

Use PowerShell from the repository root.

### 1. Configure environment variables

Create a local `.env` from the safe template:

```powershell
Copy-Item .env.example .env
```

Edit `.env` locally. Do not commit it or share its contents. The template
contains placeholders only:

```dotenv
AUDICLABS_API_KEY=
COMPLAINT_TRANSLATION_ADAPTER=
```

Set `AUDICLABS_API_KEY` to the key supplied for your assessment to enable
transcription. Keep the key on the Django backend; never put it in frontend
code. `COMPLAINT_TRANSLATION_ADAPTER` is an optional Python dotted import path
for an adapter callable. It is blank by default, and this project does not
include a real translation-provider implementation.

### 2. Set up and run Django

In a PowerShell terminal at the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Set-Location backend
python manage.py migrate
python manage.py runserver
```

The backend API is available at `http://127.0.0.1:8000/`. If PowerShell blocks
virtual-environment activation, use the Python executable directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Location backend
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py runserver
```

### 3. Set up and run React

Open a second PowerShell terminal at the repository root:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open the local URL printed by Vite, normally `http://localhost:5173/`. The
frontend currently calls the backend at `http://127.0.0.1:8000/`.
Microphone access requires browser permission; localhost is generally treated
as a secure context by modern browsers.

## API routes

- `POST /api/complaints/` — submit a multipart complaint (`customer_phone`,
  `language`, required `audio`, and optional `photo`).
- `GET /api/complaints/` — list tickets newest first; optional query filters:
  `status`, `category`, `priority`, and `language`.
- `GET /api/complaints/<ticket_uuid>/` — retrieve ticket details.
- `POST /api/complaints/<ticket_uuid>/status/` — update a ticket to one of the
  existing status values.

The queue and status-update routes are demo endpoints, not authenticated agent
access.

## Migrations and tests

Run from the `backend` directory with the virtual environment active:

```powershell
python manage.py migrate
python manage.py test complaints
```

To check whether model changes need a migration, without creating one:

```powershell
python manage.py makemigrations --check --dry-run
```

Run frontend static checks from the repository root:

```powershell
npm run lint --prefix frontend
npm run build --prefix frontend
```

The Django tests mock external services; they do not verify live AudicLabs
credentials or provider behavior. The frontend has lint/build scripts but no
dedicated automated test script.

## Current limitations

- **Translation:** No real translation provider is implemented or configured.
  Indian-language transcripts are not automatically translated into English
  unless a compatible adapter is supplied.
- **Reverse translation and replies:** Agent replies and English-to-customer-
  language translation are not implemented.
- **Agent reply text-to-speech:** Not implemented.
- **Authentication and authorization:** The demo agent queue and status update
  have no agent login or role checks. Do not expose them as a production agent
  system.
- **Audio denoising:** Not implemented.
- **Notifications:** No email, SMS, or push notifications.
- **Analytics and supervisor tools:** No reporting dashboard, supervisor
  reassignment, or agent performance tracking.
- **Storage:** Uploaded files use local Django media storage, not object
  storage. The default SQLite database and local media are intended for
  development/demo use.
- **Phone verification:** The backend validates the 10-digit format only; it
  does not verify ownership.
- **Suggestions:** Classification is simple English keyword-based logic. It
  may be inaccurate, especially for untranslated Indian-language text.

## Security and privacy

- Keep `.env` and provider credentials private. The root `.env` is ignored by
  Git; commit only safe placeholders in `.env.example`.
- AudicLabs receives uploaded complaint audio when transcription is enabled.
  Review the assessment's privacy and data-handling requirements before
  submitting real customer recordings.
- The application stores phone numbers and uploaded audio/photos in its local
  database/media directory. Protect those files and use synthetic data for
  demos whenever possible.
- Complaint API responses omit the customer's phone number and uploaded file
  URLs, but the application does not provide authentication, authorization,
  rate limiting, or phone-number verification.
- **Do not deploy with the current development settings.** Before deployment,
  replace the development `SECRET_KEY` with a securely managed secret, disable
  `DEBUG`, configure allowed hosts and production security settings, and add
  authentication/authorization and protected storage.

## Assessment demo checklist

- [ ] Configure the AudicLabs key locally in the root `.env`; do not expose it
  in React or screenshots.
- [ ] Apply database migrations and start Django and Vite in separate
  terminals.
- [ ] Confirm the browser can access the microphone and record a short,
  non-sensitive test complaint.
- [ ] If authorized, submit the test recording and check the returned ticket
  number, transcript, and transcription/processing status.
- [ ] Demonstrate ticket lookup and the demo queue, including a status filter
  and status update.
- [ ] Explain that suggestions are unreviewed, translation is unavailable
  without a configured provider, and the agent queue is not authenticated.
- [ ] Use synthetic/demo data; do not imply missing features are available.
