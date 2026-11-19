# Nexus AI — Personal Intelligence Workspace

Nexus ek personal workspace hai jo user ke apne Gmail, Google Calendar, Google Classroom aur Engineering College Ajmer notices ko ek jagah dikhata hai. Iska maqsad hai: **aaj kya zaroori hai, kaunsi deadline aa rahi hai, aur kaunsi nayi update dekhni chahiye** — ye jaldi samajhna.

## Abhi kya milta hai

- **Home:** priorities, today's schedule, workload counts, recent mail aur synced study updates.
- **Tasks & Goals:** manually task banana, email/source se nikle suggestions review karna, status aur deadline track karna.
- **Insights:** source-backed email/thread, Classroom aur college notice analysis; unsupported data ko fact nahi maana jata.
- **Connections:** Gmail/Calendar sync, Classroom permission/status, college notice check aur LinkedIn profile connection.
- **Calendar:** Google events aur task deadlines ko Month/Week/Day views mein dekhna.
- **Settings:** appearance, local notification preferences, account privacy aur consented AI provider check.

Nexus mein general-purpose chatbot/Ask Nexus screen ya email reply-draft feature nahi hai. Email dekhna, thread timeline, task extraction aur source analysis alag features ke roop mein kaam karte hain. Gemini optional hai; enabled consent ke bina personal source text Google ko nahi bheja jata. Rules-based features model unavailable hone par bhi chal sakte hain.

## LinkedIn: kya connect hoga

LinkedIn connector **Sign In with LinkedIn using OpenID Connect** ke supported scopes `openid`, `profile`, `email` maangta hai. Callback ID token ka signature, issuer, audience, expiry aur nonce validate karta hai; access token encrypted form mein save hota hai. Profile sync/status/disconnect account-scoped hain. LinkedIn page sidebar mein **LinkedIn** ke naam se professional hub deta hai aur returned profile snapshot (headline ho toh headline samet) dikhata hai.

Abhi available profile ke fields LinkedIn ke authorized OIDC response tak limited hain: member subject, name, optional headline, email/email verification, profile picture aur locale. Nexus unhe tabhi dikhata hai jab LinkedIn response mein de. LinkedIn messages, feed aur connections list current app permission se available nahi hain. LinkedIn ka Connections API first-degree connections ke liye `r_1st_connections` approval maangta hai; member posts ke liye restricted `r_member_social` approval chahiye. In APIs ke liye Nexus connector implementation bhi alag se karni hogi; professional hub is limit ko clearly dikhata hai aur fake network activity nahi banata. Refresh token LinkedIn sirf eligible approved apps ko de sakta hai; Nexus usse sirf response mein milne par store/use karta hai.

### LinkedIn Developer Portal setup

1. [LinkedIn Developer Portal](https://developer.linkedin.com/) mein apna existing `Nexus AI` app kholen ya zaroorat ho to app banayein. Koi company/page detail tabhi dein jab woh aapke liye sach ho. Verification, approval ya agreement aaye toh us step ko aap khud complete karein.
2. App ke **Products** mein **Sign In with LinkedIn using OpenID Connect** request/enable karein; `openid`, `profile`, `email` scopes approve hone chahiye.
3. Redirect URL bilkul ye register karein: `http://localhost:3000/api/auth/linkedin/callback`. HTTPS/prod environment mein apne actual host ka matching callback alag register karein.
4. LinkedIn se mile Client ID aur Client Secret sirf `backend/.env` mein rakhein:

   ```dotenv
   LINKEDIN_CLIENT_ID=your-client-id
   LINKEDIN_CLIENT_SECRET=your-client-secret
   LINKEDIN_REDIRECT_URI=http://localhost:3000/api/auth/linkedin/callback
   ```

5. Backend restart karein. Google ke existing OAuth ID/secret ya redirect ko LinkedIn values se replace na karein.

OAuth web flow mein state cookie aur nonce use hote hain. LinkedIn ka documented PKCE route native loopback clients ke liye hai; Nexus existing confidential web-client callback use karta hai, isliye native-only endpoint ko web flow par nahi lagata.

**Live LinkedIn authorization abhi prove nahi hua:** portal product approval, app credentials aur real OAuth callback ko account owner ke portal setup ke baad verify karna hoga. Agar LinkedIn company verification ya koi personal confirmation maange, user ko wahi manual step karna hai; Nexus fake information submit nahi karega.

## Local run — Windows / VS Code

Project ko localhost par chalayen. Is setup mein server doosre network devices ke liye expose nahi hota. Do VS Code terminals kholen.

### Backend terminal

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\backend
python -m venv .venv-local
.\.venv-local\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv-local\Scripts\python.exe -m uvicorn main:app --reload --reload-exclude .venv-local --reload-exclude .venv --host 127.0.0.1 --port 8000
```

`python` available na ho to VS Code Command Palette mein **Python: Create Environment** se installed Python 3.11+ chunein. Purana `.venv` kisi aur machine/path ka ho sakta hai; naya `.venv-local` use karein.

### Frontend terminal

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\frontend
npm ci
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Chrome mein [http://localhost:3000](http://localhost:3000) kholen aur Google se sign in karein. Demo login sample data dikhata hai; real account ke sync ka proof nahi hai.

## Environment setup

`backend/.env.example` starter template hai. `backend/.env` local secrets ke liye hai aur source control mein commit nahi hona chahiye.

- **Google:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`. Google Cloud project mein Gmail, Calendar aur required Classroom APIs enabled hon. Google callback exactly `http://localhost:3000/api/auth/google/callback` ho.
- **LinkedIn:** `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_REDIRECT_URI` — upar diye LinkedIn callback se exact match.
- **Gemini:** `GEMINI_API_KEY` server par; user account mein AI consent alag se on ho. Settings ka fixed connection check sirf `READY` test prompt bhejta hai, personal data nahi.
- **Workspace:** `SECRET_KEY` local secret, `DATABASE_URL` legacy Nexus database, `TIMEZONE=Asia/Kolkata`, aur zaroorat par `PIDS_DATABASE_URL` / `PIDS_VAULT_DIR`.

Koi token, API key, OAuth client secret, ya `.env` value chat, screenshot, frontend code ya Git mein paste na karein. Agar secret share ho chuka ho to provider portal mein rotate karein.

## Data aur sync kaise kaam karte hain

- Gmail read-only scope se received mail fetch hoti hai; sync initial backfill ko batches mein resume karta hai. Sent, spam aur trash ko default action extraction se exclude kiya jata hai.
- Calendar events read hote hain; event create/edit sirf user ke save action par Google Calendar mein likha jata hai. Calendar edit permission ke liye Google ko dobara authorize karna hota hai. Gmail, Classroom aur college notices read-only hain; Nexus email send, classwork submit ya grades change nahi karta.
- College notices public official circular list se fetch hote hain. Last-success timestamps successful fetch par hi aage badhte hain; failed/never-checked states alag rehte hain.
- Source suggestions pehle review ke liye aate hain; verified task banane ka faisla user karta hai.
- LinkedIn token `SECRET_KEY` se derived Fernet encryption ke saath database mein store hota hai. Disconnect karne par Nexus ke LinkedIn profile/token row delete hote hain; provider-side grant revoke tabhi claim hota jab LinkedIn ka supported revoke action available ho.
- Legacy Nexus database aur isolated PIDS database/vault alag hain. Database change se pehle exact path/revision check, verified backup aur disposable-copy rehearsal zaroor karein.

## Database update — existing data ko bachakar

New schema head `0014_remove_discord_connection` hai. Migrations app start hote waqt khud apply nahi hoti. Existing database ko **backup aur disposable copy par rehearsal** ke baad hi upgrade karein. This migration removes the retired Discord connection table and its stored provider tokens; prior migration revisions remain in the chain so existing databases can upgrade safely. Retired chatbot history tables are preserved.

Backend terminal se:

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\backend
.\.venv-local\Scripts\python.exe -m alembic upgrade head
```

Fresh database ke liye bhi isi migration chain se schema banta hai. PIDS database ke apne migrations alag hain; in legacy Nexus migrations se unhe migrate na karein.

## API map

- `GET /api/auth/linkedin/connect` — authenticated owner ke liye LinkedIn authorization start.
- `GET /api/auth/linkedin/callback` — state/nonce, OIDC token validation, profile retrieval aur encrypted persistence.
- `GET /api/linkedin/status` — safe connection/profile status; kabhi token nahi.
- `POST /api/linkedin/sync` — current authorized basic profile refresh.
- `DELETE /api/linkedin/connection` — current account ke local LinkedIn connection/token data remove.
- `/linkedin` — frontend professional hub; profile snapshot/sync aur permission-gated network feature explanation.
- Existing `/api/auth/google/*`, `/api/sync`, `/api/tasks`, `/api/emails`, `/api/events`, `/api/intelligence/*` routes Gmail/Calendar and workspace functions ke liye hain.

## Development checks

LinkedIn OAuth unit/integration behavior synthetic tokens and mocked provider responses se test kiya ja sakta hai; real product approval aur live provider authorization ka substitute nahi. Is checkout ke Next.js version ke framework-specific instructions `frontend/node_modules/next/dist/docs/` mein hote hain. Generated framework instruction files dev server dobara create kar sakta hai.

```powershell
Set-Location D:\NexusRecovery\nexus-ai-project\backend
.\.venv-local\Scripts\python.exe -m pytest -q

Set-Location D:\NexusRecovery\nexus-ai-project\frontend
npm run lint
npx tsc --noEmit --incremental false
npm run build
```

Ye checks local/mocked behavior verify karte hain, real Gmail, Classroom, LinkedIn ya Gemini account ka successful access prove nahi karte.

## Architecture

System ke components aur data flow: [ARCHITECTURE.md](ARCHITECTURE.md).
