# Setup

Five one-time steps. After this, collecting and publishing run on their own and your only regular job is the review queue.

## 1. Let Claude push code to this repo
Open https://github.com/apps/claude/installations/select_target, pick **thrivehivecollab-stack**, and under repository access add **transparency-project**. Save.

## 2. Turn on the website
In this repo on GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
Your site will live at https://thrivehivecollab-stack.github.io/transparency-project/

## 3. Database (Claude does most of this)
Claude creates a free Supabase project, loads `supabase/migrations/001_init.sql`, adds your email as the reviewer, and fills in `config.json`.
Then you do one setting in the Supabase dashboard:
**Authentication → URL Configuration**
- Site URL: `https://thrivehivecollab-stack.github.io/transparency-project/`
- Redirect URLs: add `https://thrivehivecollab-stack.github.io/transparency-project/admin.html`

## 4. Add the one secret key
In Supabase: **Project Settings → API Keys** → copy the **service_role / secret** key. Never paste it anywhere else, including chat.
In GitHub: **Settings → Secrets and variables → Actions → New repository secret**
- Name: `SUPABASE_SERVICE_KEY`
- Value: the key you copied

Optional: a free FEC key from https://api.data.gov/signup/ saved the same way as `FEC_API_KEY` raises the FEC limit from 30 to 1,000 requests an hour.

## 5. First run
GitHub → **Actions → Collect public records → Run workflow**. Then **Publish site → Run workflow**.

## Daily use
- Review queue: `https://thrivehivecollab-stack.github.io/transparency-project/admin.html` (sign in with your email link; works on your phone)
- Approved items go live within about 30 minutes.
- If a collector breaks, GitHub emails you and the run shows red under Actions. Bring it to Claude.

## Before a big public launch
- Free Cloudflare account in front of the site for unlimited traffic and poll bot protection
- Optional custom domain
- Call the Reporters Committee for Freedom of the Press legal hotline
