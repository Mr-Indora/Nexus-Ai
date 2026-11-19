# Nexus AI — Personal Intelligence Workspace

Nexus is a personal workspace that brings together the user's Gmail, Google Calendar, Google Classroom, and Government Engineering College Ajmer notices in one place.

Its primary objective is to help users quickly understand **what requires attention today, which deadlines are approaching, and which new updates they should review.**

## Current Features

- **Home:** Displays priorities, today's schedule, workload counts, recent emails, and synced study updates.
- **Tasks & Goals:** Create tasks manually, review suggestions extracted from emails or other sources, and track their status and deadlines.
- **Insights:** Provides source-backed analysis of emails, email threads, Classroom updates, and college notices. Unsupported information is never treated as factual.
- **Connections:** Supports Gmail and Calendar synchronization, Classroom permission and connection status, college notice checks, and LinkedIn profile connections.
- **Calendar:** Displays Google Calendar events and task deadlines through Month, Week, and Day views.
- **Settings:** Provides appearance preferences, local notification settings, account privacy controls, and consent-based AI provider checks.

Nexus does not include a general-purpose chatbot, an Ask Nexus screen, or an email reply-drafting feature. Email viewing, thread timelines, task extraction, and source analysis operate as separate features.

Gemini is optional. Personal source content is not sent to Google unless the user explicitly enables the required consent. Rules-based features continue to work even when an AI model is unavailable.

## LinkedIn Integration: What Gets Connected

The LinkedIn connector uses **Sign In with LinkedIn using OpenID Connect** and requests the supported scopes:

- `openid`
- `profile`
- `email`

The OAuth callback validates the ID token's signature, issuer, audience, expiry, and nonce. The access token is stored in encrypted form.

Profile synchronization, connection status, and disconnection are scoped to the authenticated account.

The LinkedIn page provides a professional hub in the Nexus sidebar under the name **LinkedIn**. It displays the returned profile snapshot, including the headline when available.

### Currently Supported LinkedIn Profile Data

Nexus is limited to the fields provided by LinkedIn's authorized OpenID Connect response:

- Member subject identifier
- Name
- Optional professional headline
- Email address and email verification status
- Profile picture
- Locale

Nexus displays these fields only when LinkedIn includes them in its response.

LinkedIn messages, feed content, and connections lists are not currently available through the application's existing permissions.

Access to LinkedIn's Connections API requires approval for the `r_1st_connections` permission for first-degree connections. Access to member posts requires the restricted `r_member_social` permission.

These APIs would also require separate connector implementation within Nexus. The professional hub clearly communicates these permission limitations and does not generate fake network activity.

LinkedIn provides refresh tokens only to eligible approved applications. Nexus stores and uses a refresh token only when LinkedIn actually provides one in its response.

### LinkedIn Developer Portal Setup

1. Open the [LinkedIn Developer Portal](https://developer.linkedin.com/) and select your existing `Nexus AI` application, or create a new one if required. Provide company or page information only if it is accurate. Complete any verification, approval, or agreement steps yourself if requested.

2. Under the application's **Products** section, request or enable **Sign In with LinkedIn using OpenID Connect**. The following scopes must be approved:
   - `openid`
   - `profile`
   - `email`

3. Register the following exact redirect URL:

   `http://localhost:3000/api/auth/linkedin/callback`

   For HTTPS or production environments, register a separate callback URL matching your actual deployment host.

4. Store the Client ID and Client Secret received from LinkedIn only in `backend/.env`:

   ```dotenv
   LINKEDIN_CLIENT_ID=your-client-id
   LINKEDIN_CLIENT_SECRET=your-client-secret
   LINKEDIN_REDIRECT_URI=http://localhost:3000/api/auth/linkedin/callback
   ```

5. Restart the backend. Do not replace your existing Google OAuth Client ID, Client Secret, or redirect URI with LinkedIn credentials.

The OAuth web flow uses state cookies and a nonce.

LinkedIn's documented PKCE flow is intended for native loopback clients. Nexus uses an existing confidential web-client callback, so the native-only endpoint is not applied to the web flow.

**Live LinkedIn authorization has not yet been verified.**

Actual product approval, application credentials, and a successful OAuth callback must be verified after the account owner completes the required setup in the LinkedIn Developer Portal.

If LinkedIn requests company verification or any personal confirmation, the user must complete that step manually. Nexus will never submit fabricated information.

## Running Nexus AI Locally — Windows / VS Code

Run the project locally using localhost. This configuration does not expose the servers to other devices on the network.

Open two terminals in VS Code.

### Backend Terminal

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\backend
python -m venv .venv-local
.\.venv-local\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv-local\Scripts\python.exe -m uvicorn main:app --reload --reload-exclude .venv-local --reload-exclude .venv --host 127.0.0.1 --port 8000
```

If Python is not available through the `python` command, open the VS Code Command Palette and select **Python: Create Environment**, then choose an installed Python 3.11+ interpreter.

An existing `.venv` may belong to another machine or project path. Use the newly created `.venv-local` environment.

### Frontend Terminal

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\frontend
npm ci
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open [http://localhost:3000](http://localhost:3000) in Chrome and sign in with Google.

Demo login displays sample data and does not prove that synchronization with a real account is working.

## Environment Configuration

`backend/.env.example` is the starter configuration template.

`backend/.env` is intended for local secrets and must never be committed to source control.

### Google Integration

Required environment variables:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`

Enable Gmail, Calendar, and the required Classroom APIs in your Google Cloud project.

The Google callback must exactly match:

`http://localhost:3000/api/auth/google/callback`

### LinkedIn Integration

Required environment variables:

- `LINKEDIN_CLIENT_ID`
- `LINKEDIN_CLIENT_SECRET`
- `LINKEDIN_REDIRECT_URI`

The redirect URI must exactly match the LinkedIn callback specified above.

### Gemini Integration

- `GEMINI_API_KEY` must be configured on the server.
- AI consent must be separately enabled in the user's account.

The fixed connection check in Settings sends only a `READY` test prompt and does not transmit personal data.

### Workspace Configuration

The workspace uses the following configuration variables:

- `SECRET_KEY` — Local application secret.
- `DATABASE_URL` — Legacy Nexus database.
- `TIMEZONE=Asia/Kolkata`
- `PIDS_DATABASE_URL` — Optional PIDS database configuration.
- `PIDS_VAULT_DIR` — Optional PIDS vault directory.

Never paste tokens, API keys, OAuth client secrets, or `.env` values into chats, screenshots, frontend code, or Git repositories.

If a secret has already been shared or exposed, rotate it through the corresponding provider portal.

## Data and Synchronization

- **Gmail:** Received emails are fetched using read-only permissions. Initial backfill synchronization resumes in batches. Sent, spam, and trash folders are excluded from default task extraction.
- **Google Calendar:** Events are read from Google Calendar. Event creation and editing are performed only after the user explicitly saves an action. Editing permissions require reauthorization with Google.
- **Google Classroom:** Classroom data is read-only. Nexus does not submit classwork or modify grades.
- **College Notices:** Notices are fetched from the official public circular listing. Last-success timestamps are updated only after successful fetches. Failed and never-checked states are tracked separately.
- **Task Suggestions:** Suggestions extracted from sources are presented for review. The user decides whether to create a verified task.
- **LinkedIn Token Storage:** LinkedIn tokens are stored in the database using Fernet encryption derived from `SECRET_KEY`. Disconnecting removes Nexus's local LinkedIn profile and token records. Provider-side authorization revocation is not claimed unless a supported LinkedIn revocation action is available.
- **Database Separation:** The legacy Nexus database and the isolated PIDS database/vault are separate. Before making database changes, verify the exact path and revision, create a verified backup, and rehearse the operation on a disposable copy.

## Database Upgrade — Preserving Existing Data

The new schema head is:

`0014_remove_discord_connection`

Migrations are not automatically applied when the application starts.

Always create a backup and rehearse the migration on a disposable database copy before upgrading an existing database.

This migration removes the retired Discord connection table and its stored provider tokens. Previous migration revisions remain in the migration chain so existing databases can be upgraded safely.

Retired chatbot history tables are preserved.

Run the following command from the backend terminal:

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\backend
.\.venv-local\Scripts\python.exe -m alembic upgrade head
```

A fresh database is also initialized using the same migration chain.

PIDS has its own separate database migrations. Do not apply these legacy Nexus migrations to the PIDS database.

## API Reference

| Endpoint | Description |
|---|---|
| `GET /api/auth/linkedin/connect` | Starts LinkedIn authorization for the authenticated account owner. |
| `GET /api/auth/linkedin/callback` | Handles state and nonce validation, OIDC token validation, profile retrieval, and encrypted persistence. |
| `GET /api/linkedin/status` | Returns safe connection and profile status without exposing tokens. |
| `POST /api/linkedin/sync` | Refreshes the currently authorized basic LinkedIn profile. |
| `DELETE /api/linkedin/connection` | Removes the current account's local LinkedIn connection and token data. |
| `/linkedin` | Frontend professional hub for profile snapshots, synchronization, and permission-gated network feature explanations. |

Existing Google authentication and workspace endpoints:

- `/api/auth/google/*`
- `/api/sync`
- `/api/tasks`
- `/api/emails`
- `/api/events`
- `/api/intelligence/*`

These routes support Gmail, Google Calendar, and workspace functionality.

## Development Checks

LinkedIn OAuth unit and integration behavior can be tested using synthetic tokens and mocked provider responses.

These tests do not replace actual LinkedIn product approval or live provider authorization.

Framework-specific instructions for the installed Next.js version are available in:

`frontend/node_modules/next/dist/docs/`

Generated framework instruction files may be recreated when the development server starts again.

### Backend Tests

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\backend
.\.venv-local\Scripts\python.exe -m pytest -q
```

### Frontend Checks

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\frontend
npm run lint
npx tsc --noEmit --incremental false
npm run build
```

These checks verify local and mocked application behavior. They do not prove successful access to real Gmail, Google Classroom, LinkedIn, or Gemini accounts.

## Architecture

For a detailed explanation of the system components and data flow, see:

[ARCHITECTURE.md](ARCHITECTURE.md)
