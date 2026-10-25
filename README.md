# VoiceDesk

VoiceDesk is a demo complaint-management application for customers who want to
submit a spoken complaint in a supported language and receive a ticket number.
It combines a React customer interface with a Django backend and an AudicLabs
speech-to-text integration.

> **Assessment/demo status:** VoiceDesk includes complaint submission, ticket
> lookup, and a basic agent queue. It is not a production-ready support system.
> Agent queue, status, and reply operations require a Django staff session.
> Google Cloud Translation is available as a configurable adapter, but
> requires project setup and credentials.

## Architecture

- **Frontend:** React 19 and Vite, under `frontend/`. The customer UI records
  audio in the browser, optionally attaches a photo, submits complaints, looks
  up tickets, and shows a basic agent queue.
- **Backend:** Django 5.2 and Django REST Framework, under `backend/`.
  Complaint handling, AudicLabs transcription/TTS, Google translation
  adapters, deterministic suggestions, duplicate detection, agent replies,
  and API routes are in `backend/complaints/`.
- **Database:** SQLite at `backend/db.sqlite3` by default.
- **Uploaded files:** Django local media storage under `backend/media/` by
  default. It is not cloud/object storage.

Complaint processing currently follows this path:

1. Django validates the submitted phone-number format and uploaded file types,
   then saves the complaint and its audio/photo.
2. If `AUDICLABS_API_KEY` is configured, the backend sends the saved audio to
   the AudicLabs denoising API using the configured model. A valid cleaned WAV
   is passed in memory to speech-to-text; the original uploaded audio remains
   unchanged. If denoising fails, the failure is recorded and transcription
   falls back to the original recording.
3. The backend sends the selected audio to AudicLabs speech-to-text. A
   successful transcript is stored as `original_transcript`. The built-in
   category, priority, and summary logic produces **unreviewed suggestions**.
4. The configured translation adapter can translate supported-language
   transcripts into English. Without an adapter, project, or credentials, the
   complaint is retained and its processing status indicates translation is
   unavailable.
5. The backend checks for an earlier possible duplicate from the same phone
   number within seven days, based on matching normalized text.

## Implemented features

- Customer complaint submission with browser audio recording and optional
  photo upload.
- AudicLabs speech-to-text integration, including request-ID logging,
  timeouts, and a single retry when a supported `Retry-After` response is
  received.
- AudicLabs audio denoising with the configurable `AUDICLABS_DENOISE_MODEL`
  setting (default `audic-1`), request-ID logging, WAV validation, and a single
  `Retry-After` retry for 429/503 responses.
- Ticket UUID generation, saved transcript, ticket status, and processing
  status.
- Read-only ticket lookup by UUID.
- Newest-first complaint queue with status, category, priority, and language
  filters, plus status updates.
- Deterministic category, priority, and summary suggestions. These are
  identified as suggestions and are not human-confirmed decisions.
- Possible-duplicate detection that preserves both tickets.
- Google Cloud Translation v3 adapter for Telugu, Hindi, English, Tamil, and
  Kannada source codes, using Application Default Credentials (ADC).
- Persistent agent replies with English text, customer-language translation,
  translation/audio statuses, and MP3 audio where TTS succeeds.
- AudicLabs TTS adapter using `audic-tts-1` and a configured voice. TTS
  failures do not discard either reply text.
- Customer ticket lookup includes saved translated replies and an audio player
  when audio generation succeeded.
- Focused Django tests that mock external speech-to-text, translation, and TTS
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
AUDICLABS_DENOISE_MODEL=audic-1
AUDICLABS_TTS_MODEL=audic-tts-1
AUDICLABS_TTS_VOICE=
AUDICLABS_TTS_LANGUAGE=
COMPLAINT_TRANSLATION_ADAPTER=complaints.google_translation.translate_to_english
COMPLAINT_REPLY_TRANSLATION_ADAPTER=complaints.google_translation.translate_from_english
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=global
GOOGLE_APPLICATION_CREDENTIALS=
```

Set `AUDICLABS_API_KEY` to the key supplied for your assessment to enable
denoising and transcription. Keep the key on the Django backend; never put it
in frontend code. `AUDICLABS_DENOISE_MODEL` chooses the documented denoising
model (`audic-1` by default; `audic-1` through `audic-5` are available per the
assessment reference). `COMPLAINT_TRANSLATION_ADAPTER` is an optional Python
dotted import path; the example selects the included Google Cloud adapter.
Set `GOOGLE_CLOUD_PROJECT` to the Google Cloud project ID. Keep
`GOOGLE_CLOUD_LOCATION` as `global` unless your setup requires another
location. Translation requires the Cloud Translation API enabled and billing
enabled on the project.

For agent replies, set `AUDICLABS_TTS_VOICE` to a voice identifier available
to your AudicLabs workspace. The application uses the complaint language as
the TTS language by default. Use `AUDICLABS_TTS_LANGUAGE` only when a
workspace-specific language value is needed. Verify both values in the
authenticated AudicLabs API explorer before using TTS. Reply translation uses
`COMPLAINT_REPLY_TRANSLATION_ADAPTER` and the Google adapter by default.
`AUDICLABS_TTS_VOICE` must be set to a voice identifier available to the
workspace; consult the AudicLabs `GET /v1/tts/voices` lookup. The reply uses
the complaint's language code by default; optionally set
`AUDICLABS_TTS_LANGUAGE` to a provider-supported language value from
`GET /v1/tts/languages`. These lookup response schemas are not documented in
the public API pages checked for this implementation, so the application
does not parse or call those lookup endpoints automatically. Verify selected
values in the authenticated AudicLabs API explorer before enabling TTS.

For local development, configure Application Default Credentials (ADC) with
the Google Cloud CLI:

```powershell
gcloud auth application-default login
```

Alternatively, set `GOOGLE_APPLICATION_CREDENTIALS` to the local path of a
service-account JSON file with the required permissions. Keep that file
outside the repository, do not commit it, and never paste its contents into
`.env`. The value is a file path, not the JSON credential itself.

### 2. Set up and run Django

In a PowerShell terminal at the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Set-Location backend
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Create an agent account with Django's interactive `createsuperuser` command;
do not put account credentials in source files or environment templates.
Staff accounts can sign in to the Agent Portal. The API uses Django sessions
and CSRF protection for agent login, logout, status updates, and replies.

The backend API is available at `http://localhost:8000/`. If PowerShell blocks
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

Open the local URL printed by Vite, normally `http://localhost:5173/`. Use
`localhost` for both frontend and backend so browser session cookies remain
same-site; the frontend calls `http://localhost:8000/`.
Microphone access requires browser permission; localhost is generally treated
as a secure context by modern browsers.

## API routes

- `POST /api/complaints/` — submit a multipart complaint (`customer_phone`,
  `language`, required `audio`, and optional `photo`).
- `GET /api/auth/session/` — initialize/read the agent session and CSRF cookie.
- `POST /api/auth/login/` — authenticate a staff account with JSON
  `{"username":"...","password":"..."}`.
- `POST /api/auth/logout/` — end the current session.
- `GET /api/complaints/` — list tickets newest first (staff session required);
  optional query filters: `status`, `category`, `priority`, and `language`.
- `GET /api/complaints/<ticket_uuid>/` — retrieve ticket details.
- `POST /api/complaints/<ticket_uuid>/replies/` — save an English agent reply,
  translate it into the ticket's customer language, and attempt MP3 generation
  (staff session and CSRF token required). Send JSON such as
  `{"english_text":"Your repair is complete."}`.
- `POST /api/complaints/<ticket_uuid>/status/` — update a ticket to one of the
  existing status values (staff session and CSRF token required).

Saved agent replies are included in the complaint list and public ticket lookup
payloads. Customer submission and ticket lookup remain accessible without an
agent login.

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
credentials or provider behavior. Agent-reply tests mock translation and TTS;
no external provider calls are made by the test suite. The frontend has
lint/build scripts but no dedicated automated test script.

## Current limitations

- **Translation setup:** The Google Cloud Translation adapter is implemented,
  but translation remains unavailable until a project, API/billing setup, and
  valid ADC credentials are configured. No live provider request has been
  verified as part of these changes. Translation sends transcript text to
  Google Cloud; review data-handling and cost requirements before enabling it.
- **Agent access:** The queue, status, and reply endpoints require a logged-in
  Django staff account. This demo has no finer-grained agent roles or
  supervisor permissions.
- **Agent reply providers:** Configure a valid AudicLabs voice identifier and
  ensure the Google Cloud reverse-translation adapter is available. Replies
  are retained when translation or audio generation fails; status fields show
  which stages completed.
- **TTS metadata:** The public AudicLabs documentation confirms MP3 output but
  does not publish the exact success `Content-Type`, audio headers, or lookup
  response schemas. The adapter consumes the response body as binary and
  checks for an MP3 signature; the voice and optional language must be
  configured using values verified in the authenticated API explorer.
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
- English agent replies are sent to Google Cloud Translation and translated
  reply text is sent to AudicLabs TTS when those integrations are configured.
  Review provider privacy, retention, and billing requirements before use.
- The application stores phone numbers and uploaded audio/photos in its local
  database/media directory. Protect those files and use synthetic data for
  demos whenever possible.
- Complaint API responses omit the customer's phone number and uploaded file
  URLs. Agent-only queue and write operations require a staff session; public
  ticket lookup, complaint submission, rate limiting, and phone-number
  verification remain unauthenticated/unavailable.
- **Do not deploy with the current development settings.** Before deployment,
  replace the development `SECRET_KEY` with a securely managed secret, disable
  `DEBUG`, configure allowed hosts and production security settings, and review
  authentication, authorization, and protected storage for production needs.

## Assessment demo checklist

- [ ] Configure the AudicLabs key locally in the root `.env`; do not expose it
  in React or screenshots.
- [ ] Apply database migrations and start Django and Vite in separate
  terminals.
- [ ] Confirm the browser can access the microphone and record a short,
  non-sensitive test complaint.
- [ ] If authorized, submit the test recording and check the returned ticket
  number, transcript, and transcription/processing status.
- [ ] Create an agent account with `python manage.py createsuperuser`, then
  sign in to demonstrate the protected queue, status filter, and status update.
- [ ] If configured and authorized, submit a synthetic English agent reply and
  demonstrate the translated reply and generated MP3 playback.
- [ ] Explain that suggestions are unreviewed, Google translation requires
  project credentials and may incur charges, and TTS requires a configured
  voice. The portal is a demo, not production security.
- [ ] Use synthetic/demo data; do not imply missing features are available.
