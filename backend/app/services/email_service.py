"""
email_service.py
Real SMTP email sender — works inside Docker behind corporate proxy/VPN.

Root cause of SSL error:
  Corporate networks / VPNs install a self-signed root CA and intercept TLS.
  Docker containers don't trust this CA → CERTIFICATE_VERIFY_FAILED.
  Fix: use ssl.CERT_NONE for the SMTP connection (email payload is still
  encrypted in transit, we just skip chain-of-trust verification).

GMAIL SETUP:
  1. Google Account → Security → Enable 2-Step Verification
  2. Google Account → Security → App Passwords → create one → copy 16-char password
  3. .env:  SMTP_PORT=465, SMTP_USER=you@gmail.com, SMTP_PASSWORD=xxxx xxxx xxxx xxxx
"""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from app.config import settings


# ── SSL context that works behind corporate proxies ───────────────────────────
def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False          # skip hostname verification
    ctx.verify_mode    = ssl.CERT_NONE  # skip certificate chain verification
    return ctx


# ── HTML Templates ────────────────────────────────────────────────────────────

def _build_outreach_html(lead_name: str, company: str, agent_name: str, notes: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;margin:0;padding:0}}
.container{{max-width:560px;margin:40px auto;background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden}}
.header{{background:#1d4ed8;padding:28px 32px}}.header h1{{color:#fff;margin:0;font-size:20px;font-weight:700}}
.header p{{color:#bfdbfe;margin:4px 0 0;font-size:13px}}.body{{padding:32px}}
.body p{{color:#374151;font-size:15px;line-height:1.7;margin:0 0 16px}}
.highlight{{background:#eff6ff;border-left:3px solid #3b82f6;padding:12px 16px;border-radius:0 8px 8px 0;margin:20px 0;font-size:14px;color:#1e40af}}
.footer{{padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8}}
</style></head><body>
<div class="container">
  <div class="header"><h1>Sales Cadence Engine</h1><p>Automated Outreach · Stage 2</p></div>
  <div class="body">
    <p>Hi <strong>{lead_name}</strong>,</p>
    <p>I'm reaching out from <strong>{company or "our team"}</strong> — we believe we can genuinely help your business.</p>
    <div class="highlight">📋 <strong>Context:</strong> {notes or "We have an exciting opportunity to discuss with you."}</div>
    <p>Would love a quick 15-minute call. Would any of these work?</p>
    <p>&nbsp;&nbsp;📅 <strong>Monday</strong> — 10 AM or 3 PM IST<br>
       &nbsp;&nbsp;📅 <strong>Tuesday</strong> — 11 AM or 4 PM IST<br>
       &nbsp;&nbsp;📅 <strong>Wednesday</strong> — 10 AM or 2 PM IST</p>
    <p>Simply reply with your preferred time!</p>
    <p>Warm regards,<br><strong>{agent_name}</strong><br>Sales Cadence Engine Team</p>
  </div>
  <div class="footer">To unsubscribe, reply with "STOP".</div>
</div></body></html>"""


def _build_followup_html(lead_name: str, call_outcome: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;margin:0;padding:0}}
.container{{max-width:560px;margin:40px auto;background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden}}
.header{{background:#0f766e;padding:28px 32px}}.header h1{{color:#fff;margin:0;font-size:20px;font-weight:700}}
.header p{{color:#99f6e4;margin:4px 0 0;font-size:13px}}.body{{padding:32px}}
.body p{{color:#374151;font-size:15px;line-height:1.7;margin:0 0 16px}}
.outcome-box{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px;margin:20px 0;font-size:14px;color:#166534}}
.footer{{padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8}}
</style></head><body>
<div class="container">
  <div class="header"><h1>Following Up — Great Speaking With You!</h1><p>Post-call follow-up · Sales Cadence Engine</p></div>
  <div class="body">
    <p>Hi <strong>{lead_name}</strong>,</p>
    <p>Thank you for taking the time to connect with us today!</p>
    <div class="outcome-box">✅ <strong>Call Summary:</strong> {call_outcome or "We had a productive conversation."}</div>
    <p>Following up on the next steps we discussed. Please reply if you have any questions.</p>
    <p>Best regards,<br><strong>Sales Team</strong><br>Sales Cadence Engine</p>
  </div>
  <div class="footer">Automated follow-up after a successful call.</div>
</div></body></html>"""


def _build_retry_exhausted_html(lead_name: str, attempt_count: int) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;margin:0;padding:0}}
.container{{max-width:560px;margin:40px auto;background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden}}
.header{{background:#7c3aed;padding:28px 32px}}.header h1{{color:#fff;margin:0;font-size:20px;font-weight:700}}
.header p{{color:#ddd6fe;margin:4px 0 0;font-size:13px}}.body{{padding:32px}}
.body p{{color:#374151;font-size:15px;line-height:1.7;margin:0 0 16px}}
.info-box{{background:#faf5ff;border:1px solid #e9d5ff;border-radius:8px;padding:16px;margin:20px 0;font-size:14px;color:#6b21a8}}
.footer{{padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8}}
</style></head><body>
<div class="container">
  <div class="header"><h1>We Tried Reaching You {attempt_count}x</h1><p>Alternative contact · Sales Cadence Engine</p></div>
  <div class="body">
    <p>Hi <strong>{lead_name}</strong>,</p>
    <p>We tried calling you <strong>{attempt_count} times</strong> but couldn't connect — completely understand you're busy!</p>
    <div class="info-box">📞 <strong>{attempt_count} call attempts</strong> made. This email is our alternative way to connect.</div>
    <p>We have something that could genuinely benefit you. Reply and we'll set up a time that works for you.</p>
    <p>Thank you,<br><strong>Sales Team</strong><br>Sales Cadence Engine</p>
  </div>
  <div class="footer">Sent after {attempt_count} unsuccessful call attempts. Reply STOP to unsubscribe.</div>
</div></body></html>"""


# ── Core sender ───────────────────────────────────────────────────────────────

def send_email_sync(to_email: str, to_name: str, subject: str, html_body: str) -> dict:
    """
    Sends real email via SMTP.
    No credentials → simulates (safe for dev/demo).
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        print(f"[EMAIL SIMULATED] To: {to_email} | Subject: {subject}")
        return {"success": True, "simulated": True, "to": to_email}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    msg["To"]      = f"{to_name} <{to_email}>"
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    ctx = _make_ssl_context()  # cert verification disabled — works behind VPN/proxy

    # ── Port 465: SSL from first byte (recommended for Docker) ────────────────
    if settings.SMTP_PORT == 465:
        try:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, 465, context=ctx, timeout=15) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.EMAIL_FROM, to_email, msg.as_string())
            return {"success": True, "to": to_email, "method": "SSL-465"}
        except Exception as e:
            return {"success": False, "error": str(e), "to": to_email}

    # ── Port 587: STARTTLS ────────────────────────────────────────────────────
    try:
        with smtplib.SMTP(settings.SMTP_HOST, 587, timeout=15) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.EMAIL_FROM, to_email, msg.as_string())
        return {"success": True, "to": to_email, "method": "STARTTLS-587"}
    except Exception as e:
        return {"success": False, "error": str(e), "to": to_email}


# ── Public helpers ────────────────────────────────────────────────────────────

def send_outreach_email(lead_name: str, lead_email: str, company: str, notes: str) -> dict:
    html = _build_outreach_html(lead_name, company, "Sales Team", notes)
    return send_email_sync(lead_email, lead_name, f"Quick question for {lead_name} at {company}", html)


def send_followup_email(lead_name: str, lead_email: str, call_outcome: str) -> dict:
    html = _build_followup_html(lead_name, call_outcome)
    return send_email_sync(lead_email, lead_name, f"Following up on our conversation, {lead_name}", html)


def send_retry_exhausted_email(lead_name: str, lead_email: str, attempt_count: int) -> dict:
    html = _build_retry_exhausted_html(lead_name, attempt_count)
    return send_email_sync(lead_email, lead_name, f"We tried calling you {attempt_count}x — let's connect", html)



































