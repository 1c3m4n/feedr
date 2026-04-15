# feedr

A modern recreation of Google Reader — an RSS feed aggregator with a clean, fast UI.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your Google OAuth credentials

# Run the app
uvicorn main:app --reload
```

## Health Check

```bash
curl http://localhost:8000/up
```

Returns:
```json
{
  "status": "ok",
  "service": "feedr",
  "timestamp": "2026-04-15T...",
  "version": "0.1.0"
}
```

## Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create OAuth 2.0 credentials
3. Add `http://localhost:8000/auth/callback` as an authorized redirect URI
4. Copy the Client ID and Client Secret into your `.env` file

## Features (Planned)

- Google OAuth login
- Feed subscription and management
- Unread/read article tracking
- Clean, responsive reader UI
