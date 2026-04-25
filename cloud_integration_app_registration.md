# Cloud Integration App Registration Guide

How to register developer apps for Google Drive and Microsoft SharePoint, for use with the docling platform's cloud storage integration.

---

## Part 1 — Google Drive (Google Cloud Console)

### What you'll end up with
- A **Client ID** and **Client Secret** to store in your `.env`
- An OAuth consent screen that users see when they connect their Google account

---

### Step 1 — Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Sign in with the Google account you want to own this app (use your company/product account, not a personal one)
3. Click the project dropdown at the top → **New Project**
4. Name it something like `docling-platform` → click **Create**
5. Make sure the new project is selected in the dropdown before continuing

---

### Step 2 — Enable the Google Drive API

1. In the left sidebar go to **APIs & Services → Library**
2. Search for **Google Drive API**
3. Click it → click **Enable**

---

### Step 3 — Configure the OAuth Consent Screen

This is what users see when they click "Connect Google Drive".

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** (allows any Google account to connect) → click **Create**
3. Fill in the required fields:
   - **App name**: `docling` (or your client-facing brand name)
   - **User support email**: your support email
   - **App logo**: optional at this stage, required before production verification
   - **Developer contact email**: your email
4. Click **Save and Continue**
5. On the **Scopes** step, click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/drive.readonly`
   - `https://www.googleapis.com/auth/drive.metadata.readonly`
6. Click **Update → Save and Continue**
7. On the **Test users** step, add the email addresses you'll use during development (up to 100 users allowed while in Testing mode)
8. Click **Save and Continue → Back to Dashboard**

> **Note on publishing:** While in "Testing" mode, only your listed test users can connect. When you're ready for real clients, you'll need to click **Publish App** and go through Google's OAuth verification review. This requires a privacy policy URL, a demo video, and justification for your requested scopes. Allow 1–4 weeks.

---

### Step 4 — Create OAuth 2.0 Credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. Application type: **Web application**
4. Name: `docling-web` (just a label)
5. Under **Authorised redirect URIs**, add:
   - `http://localhost:8080/integrations/google/callback` (for local dev)
   - `https://your-vercel-domain.vercel.app/integrations/google/callback` (for staging)
   - `https://yourclientdomain.com/integrations/google/callback` (for production, add per-client as needed)
6. Click **Create**
7. A dialog will show your **Client ID** and **Client Secret** — copy both immediately and store them securely

---

### Step 5 — Add to your .env

```
GOOGLE_CLIENT_ID=your-client-id-here
GOOGLE_CLIENT_SECRET=your-client-secret-here
GOOGLE_REDIRECT_URI=http://localhost:8080/integrations/google/callback
```

---

## Part 2 — Microsoft SharePoint (Azure AD / Microsoft Entra)

### What you'll end up with
- A **Client ID** (Application ID), **Client Secret**, and **Tenant ID** for your `.env`
- A **multi-tenant** app registration so users from any company's Microsoft 365 account can connect

---

### Step 1 — Create an Azure Account and Access Entra

1. Go to [portal.azure.com](https://portal.azure.com)
2. Sign in with a Microsoft account (use a work/company account if possible — this will be the owner of the app registration)
3. In the search bar at the top, search for **Microsoft Entra ID** and open it
4. In the left sidebar, click **App registrations**

---

### Step 2 — Register a New Application

1. Click **+ New registration**
2. Fill in:
   - **Name**: `docling-platform`
   - **Supported account types**: choose **Accounts in any organizational directory (Any Microsoft Entra ID tenant) and personal Microsoft accounts**
     > This is the multi-tenant option — critical for a SaaS platform where each client has their own Microsoft 365 tenant
   - **Redirect URI**: select **Web** from the dropdown, then enter:
     `http://localhost:8080/integrations/microsoft/callback`
3. Click **Register**
4. You'll land on the app's Overview page — copy the **Application (client) ID** and the **Directory (tenant) ID** (you'll need both)

---

### Step 3 — Add Additional Redirect URIs

1. In the left sidebar click **Authentication**
2. Under **Web → Redirect URIs**, add your additional environments:
   - `https://your-vercel-domain.vercel.app/integrations/microsoft/callback`
   - `https://yourclientdomain.com/integrations/microsoft/callback`
3. Under **Implicit grant and hybrid flows**, leave everything unchecked (you're using the standard auth code flow)
4. Click **Save**

---

### Step 4 — Create a Client Secret

1. In the left sidebar click **Certificates & secrets**
2. Click **+ New client secret**
3. Description: `docling-secret`
4. Expiry: choose **24 months** (you'll need to rotate this before it expires)
5. Click **Add**
6. **Copy the secret Value immediately** — it is only shown once. Store it securely alongside your Client ID.

---

### Step 5 — Grant API Permissions

1. In the left sidebar click **API permissions**
2. Click **+ Add a permission → Microsoft Graph → Delegated permissions**
3. Search for and add the following permissions:
   - `Files.Read` — read the signed-in user's files
   - `Files.Read.All` — read all files the user has access to
   - `Sites.Read.All` — read SharePoint sites and document libraries
   - `offline_access` — allows refresh tokens so users don't have to re-authenticate constantly
   - `User.Read` — read basic profile info (usually already added by default)
4. Click **Add permissions**
5. You do **not** need to click "Grant admin consent" — these are delegated permissions, so each user grants consent individually when they connect their account

---

### Step 6 — Publisher Verification (important for enterprise clients)

Without this, users from enterprise Microsoft 365 tenants will see a warning banner saying the app publisher is unverified, which may cause their IT department to block it.

1. In the left sidebar click **Branding & properties**
2. Under **Publisher domain**, verify your domain
3. To get the blue "verified" badge, you need a **Microsoft Partner Network (MPN) account** linked to your Azure account — visit [partner.microsoft.com](https://partner.microsoft.com) to set one up

> Publisher verification is not required for development and testing, but is strongly recommended before rolling out to enterprise clients.

---

### Step 7 — Add to your .env

```
MICROSOFT_CLIENT_ID=your-application-client-id-here
MICROSOFT_CLIENT_SECRET=your-client-secret-value-here
MICROSOFT_TENANT_ID=common
MICROSOFT_REDIRECT_URI=http://localhost:8080/integrations/microsoft/callback
```

> Use `common` as the tenant ID for multi-tenant apps — this tells MSAL to accept tokens from any Azure AD tenant, not just your own.

---

## Summary — What to Store in .env

| Variable | Source |
|---|---|
| `GOOGLE_CLIENT_ID` | Google Cloud Console → Credentials |
| `GOOGLE_CLIENT_SECRET` | Google Cloud Console → Credentials |
| `GOOGLE_REDIRECT_URI` | Set by you; must match what's registered |
| `MICROSOFT_CLIENT_ID` | Azure Portal → App Registration → Overview |
| `MICROSOFT_CLIENT_SECRET` | Azure Portal → Certificates & secrets |
| `MICROSOFT_TENANT_ID` | Use `common` for multi-tenant |
| `MICROSOFT_REDIRECT_URI` | Set by you; must match what's registered |

---

## Key Things to Remember

- **Google:** You must add test users manually while in Testing mode. Go through OAuth verification before launching to real clients.
- **Microsoft:** Always register as multi-tenant (`common` tenant). Use delegated permissions, not application permissions. Rotate the client secret before it expires (set a calendar reminder).
- **Redirect URIs:** Both platforms require exact URI matches — a trailing slash or wrong port will cause auth failures. Add all environments (local, staging, production) before you need them.
- **Secrets:** Never commit Client Secrets to git. Always via `.env` / environment variables.
