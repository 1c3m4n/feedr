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
uvicorn main:app --reload --port 80
```

## Health Check

```bash
curl http://localhost/up
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
3. Add `http://localhost/auth/callback` as an authorized redirect URI
4. Copy the Client ID and Client Secret into your `.env` file

## Docker / Once

```bash
docker run -d -p 80:80 \
  -e GOOGLE_CLIENT_ID=your-id \
  -e GOOGLE_CLIENT_SECRET=your-secret \
  -e SECRET_KEY=your-secret-key \
  -e APP_URL=https://your-domain.com \
  -v feedr-data:/storage \
  ghcr.io/1c3m4n/feedr:latest
```

Data is stored in `/storage` inside the container for compatibility with Once.

## Features

- Google OAuth login
- Feed subscription and management
- Unread/read article tracking
- Folders for organization
- OPML import/export
- Full-text search
- Keyboard shortcuts (j/k/m/v)
- Dark mode
- Mobile responsive layout
- Clean, responsive reader UI
