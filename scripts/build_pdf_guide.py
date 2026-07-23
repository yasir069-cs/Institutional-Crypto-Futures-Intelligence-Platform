"""
Generate the comprehensive PDF setup guide for the n8n workflow.
Output: /home/z/my-project/download/n8n_setup_guide.pdf
"""
import os
import sys
from pathlib import Path
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
    KeepTogether, Image, ListFlowable, ListItem, HRFlowable, Preformatted,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUTPUT = Path("/home/z/my-project/download/n8n_setup_guide.pdf")
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

# ─── Register fonts (Helvetica is built-in, no need for TTF) ────────────────
# Using built-in Helvetica family for simplicity and reliability

# ─── Color palette (professional technical doc) ─────────────────────────────
PAGE_BG = colors.white
TEXT_PRIMARY = colors.HexColor('#1A1A2E')
TEXT_MUTED = colors.HexColor('#666666')
HEADER_FILL = colors.HexColor('#0F3460')
ACCENT = colors.HexColor('#16213E')
BORDER = colors.HexColor('#CCCCCC')
TABLE_STRIPE = colors.HexColor('#F5F5F5')
CODE_BG = colors.HexColor('#F8F9FA')
CODE_BORDER = colors.HexColor('#E0E0E0')
WARN_BG = colors.HexColor('#FFF3CD')
WARN_BORDER = colors.HexColor('#FFC107')
SUCCESS_BG = colors.HexColor('#D4EDDA')
SUCCESS_BORDER = colors.HexColor('#28A745')
ERROR_BG = colors.HexColor('#F8D7DA')
ERROR_BORDER = colors.HexColor('#DC3545')

# ─── Styles ─────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    'CustomTitle', parent=styles['Title'],
    fontSize=28, textColor=HEADER_FILL, spaceAfter=8,
    alignment=TA_CENTER, fontName='Helvetica-Bold',
)
subtitle_style = ParagraphStyle(
    'Subtitle', parent=styles['Normal'],
    fontSize=14, textColor=TEXT_MUTED, spaceAfter=20,
    alignment=TA_CENTER, fontName='Helvetica',
)
h1_style = ParagraphStyle(
    'H1', parent=styles['Heading1'],
    fontSize=20, textColor=HEADER_FILL, spaceBefore=20, spaceAfter=12,
    fontName='Helvetica-Bold', borderWidth=0, borderPadding=0,
)
h2_style = ParagraphStyle(
    'H2', parent=styles['Heading2'],
    fontSize=15, textColor=HEADER_FILL, spaceBefore=14, spaceAfter=8,
    fontName='Helvetica-Bold',
)
h3_style = ParagraphStyle(
    'H3', parent=styles['Heading3'],
    fontSize=12, textColor=ACCENT, spaceBefore=10, spaceAfter=6,
    fontName='Helvetica-Bold',
)
body_style = ParagraphStyle(
    'Body', parent=styles['Normal'],
    fontSize=10.5, textColor=TEXT_PRIMARY, spaceAfter=8,
    alignment=TA_JUSTIFY, fontName='Helvetica', leading=15,
)
code_style = ParagraphStyle(
    'Code', parent=styles['Code'],
    fontSize=8.5, textColor=TEXT_PRIMARY, fontName='Courier',
    backColor=CODE_BG, borderColor=CODE_BORDER, borderWidth=0.5,
    borderPadding=6, spaceBefore=4, spaceAfter=8, leading=11,
)
note_style = ParagraphStyle(
    'Note', parent=body_style,
    fontSize=10, textColor=TEXT_PRIMARY, leftIndent=12, rightIndent=12,
    backColor=colors.HexColor('#E8F4FD'), borderColor=colors.HexColor('#2196F3'),
    borderWidth=0.5, borderPadding=8, spaceBefore=6, spaceAfter=10,
)
warn_style = ParagraphStyle(
    'Warn', parent=body_style,
    fontSize=10, textColor=TEXT_PRIMARY, leftIndent=12, rightIndent=12,
    backColor=WARN_BG, borderColor=WARN_BORDER,
    borderWidth=0.5, borderPadding=8, spaceBefore=6, spaceAfter=10,
)
success_style = ParagraphStyle(
    'Success', parent=body_style,
    fontSize=10, textColor=TEXT_PRIMARY, leftIndent=12, rightIndent=12,
    backColor=SUCCESS_BG, borderColor=SUCCESS_BORDER,
    borderWidth=0.5, borderPadding=8, spaceBefore=6, spaceAfter=10,
)
error_style = ParagraphStyle(
    'Error', parent=body_style,
    fontSize=10, textColor=TEXT_PRIMARY, leftIndent=12, rightIndent=12,
    backColor=ERROR_BG, borderColor=ERROR_BORDER,
    borderWidth=0.5, borderPadding=8, spaceBefore=6, spaceAfter=10,
)
toc_h1 = ParagraphStyle('TOC1', fontName='Helvetica-Bold', fontSize=11, leftIndent=0, spaceBefore=6, textColor=HEADER_FILL)
toc_h2 = ParagraphStyle('TOC2', fontName='Helvetica', fontSize=10, leftIndent=16, spaceBefore=2, textColor=TEXT_PRIMARY)


class TocDocTemplate(SimpleDocTemplate):
    """DocTemplate that feeds TOC entries."""
    def afterFlowable(self, flowable):
        if hasattr(flowable, 'bookmark_name'):
            level = getattr(flowable, 'bookmark_level', 0)
            text = getattr(flowable, 'bookmark_text', '')
            key = getattr(flowable, 'bookmark_key', '')
            self.notify('TOCEntry', (level, text, self.page, key))


def add_heading(text, style, level=0, story=None):
    """Add a heading with TOC bookmark."""
    import hashlib
    key = f'h_{hashlib.md5(text.encode()).hexdigest()[:8]}'
    p = Paragraph(f'<a name="{key}"/>{text}', style)
    p.bookmark_name = key
    p.bookmark_level = level
    p.bookmark_text = text
    p.bookmark_key = key
    if story is not None:
        story.append(p)
    return p


def add_code(text, story):
    """Add a code block (monospace, wrapped)."""
    # Escape XML special chars
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    p = Preformatted(text, code_style)
    story.append(p)


def add_table(data, story, col_widths=None):
    """Add a styled table."""
    if col_widths is None:
        page_w = A4[0] - 2 * 2 * cm
        col_widths = [page_w / len(data[0])] * len(data[0])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_FILL),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9.5),
        ('TEXTCOLOR', (0, 1), (-1, -1), TEXT_PRIMARY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, TABLE_STRIPE]),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))


# ─── Build the PDF ──────────────────────────────────────────────────────────
story = []

# ═══ COVER PAGE ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 4 * cm))
story.append(Paragraph('n8n Workflow Setup Guide', title_style))
story.append(Spacer(1, 0.5 * cm))
story.append(Paragraph('Institutional Crypto Futures Intelligence Platform', subtitle_style))
story.append(Spacer(1, 2 * cm))

# Cover info box
cover_data = [
    ['Version', '1.0'],
    ['Date', datetime.now().strftime('%B %Y')],
    ['Platform', 'n8n (self-hosted or cloud)'],
    ['Exchange', 'Binance USDT Futures (public API — no key needed)'],
    ['AI Provider', 'OpenRouter (free Gemma 4 26B)'],
    ['Alerts', 'Telegram Bot'],
    ['Scan Frequency', 'Every 60 seconds'],
    ['Pairs Scanned', 'Up to 500 (top 30 analyzed deeply)'],
]
t = Table(cover_data, colWidths=[4 * cm, 10 * cm])
t.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
    ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
    ('FONTSIZE', (0, 0), (-1, -1), 11),
    ('TEXTCOLOR', (0, 0), (0, -1), HEADER_FILL),
    ('TEXTCOLOR', (1, 0), (1, -1), TEXT_PRIMARY),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('LEFTPADDING', (0, 0), (-1, -1), 12),
    ('TOPPADDING', (0, 0), (-1, -1), 8),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ('LINEBELOW', (0, 0), (-1, -2), 0.5, BORDER),
]))
story.append(t)
story.append(Spacer(1, 3 * cm))
story.append(HRFlowable(width='80%', thickness=2, color=HEADER_FILL))
story.append(Spacer(1, 1 * cm))
story.append(Paragraph(
    '<para align="center"><i>This guide contains everything you need to import, '
    'configure, and run the Institutional Crypto Futures Intelligence Platform '
    'as an n8n workflow. Every node, every setting, every credential — explained.</i></para>',
    ParagraphStyle('CoverFooter', fontSize=10, textColor=TEXT_MUTED, alignment=TA_CENTER, leading=14)
))
story.append(PageBreak())

# ═══ TABLE OF CONTENTS ════════════════════════════════════════════════════
add_heading('Table of Contents', h1_style, level=0, story=story)
toc = TableOfContents()
toc.levelStyles = [toc_h1, toc_h2]
story.append(toc)
story.append(PageBreak())

# ═══ CHAPTER 1: Introduction & Prerequisites ══════════════════════════════
add_heading('1. Introduction & Prerequisites', h1_style, level=0, story=story)

add_heading('1.1 What This Guide Covers', h2_style, level=1, story=story)
story.append(Paragraph(
    'This guide walks you through importing and configuring the Institutional Crypto '
    'Futures Intelligence Platform as an n8n workflow. The platform scans 500 Binance '
    'USDT Futures pairs every 60 seconds, filters them through a 3-stage institutional '
    'pipeline (math filter → Smart Money Concepts → AI validation), and sends Telegram '
    'alerts only for premium setups that pass all safety rules.', body_style))
story.append(Paragraph(
    'The same logic that powers the Python platform is replicated here in n8n visual '
    'workflow form. Every node, every setting, every credential is documented. By the '
    'end of this guide, you will have a 24/7 automated trading intelligence bot running '
    'in n8n that sends institutional-grade alerts to your Telegram.', body_style))

add_heading('1.2 Why n8n?', h2_style, level=1, story=story)
story.append(Paragraph(
    'n8n is a visual workflow automation tool that makes it easy to connect APIs, '
    'run custom JavaScript code, and build complex pipelines without writing a full '
    'application. Compared to deploying the Python platform on a server, n8n offers '
    'these advantages:', body_style))
story.append(Paragraph(
    '• <b>Visual debugging</b> — see exactly which node failed and what data flowed through it.<br/>'
    '• <b>No server management</b> — n8n handles process lifecycle, retries, and scheduling.<br/>'
    '• <b>Easy credential management</b> — store API keys securely in n8n credentials.<br/>'
    '• <b>Drag-and-drop editing</b> — modify thresholds, add nodes, or remove steps without touching code.<br/>'
    '• <b>Built-in HTTP client</b> — no need to manage aiohttp sessions or rate limits manually.<br/>'
    '• <b>Execution history</b> — every workflow run is logged with full input/output data.',
    body_style))

add_heading('1.3 Prerequisites', h2_style, level=1, story=story)
story.append(Paragraph('Before you start, make sure you have:', body_style))

prereq_data = [
    ['#', 'Requirement', 'How to Get It', 'Cost'],
    ['1', 'n8n instance (self-hosted or cloud)', 'https://docs.n8n.io/hosting/ OR https://n8n.cloud', 'Free (self-hosted) / Paid (cloud)'],
    ['2', 'OpenRouter API key', 'https://openrouter.ai/ → Sign in → Keys → Create', 'Free tier available'],
    ['3', 'Telegram Bot token', 'Open Telegram → @BotFather → /newbot', 'Free'],
    ['4', 'Telegram Chat ID', 'Send message to bot → https://api.telegram.org/bot<TOKEN>/getUpdates', 'Free'],
    ['5', 'n8n workflow JSON file', 'Downloaded from this guide (n8n_workflow.json)', 'Free'],
]
add_table(prereq_data, story, col_widths=[1 * cm, 4 * cm, 7 * cm, 4 * cm])

add_heading('1.4 What You Do NOT Need', h2_style, level=1, story=story)
story.append(Paragraph(
    '• <b>Binance API key</b> — NOT required. The platform uses only public market data endpoints.<br/>'
    '• <b>AWS EC2 server</b> — NOT required if using n8n cloud. Self-hosted n8n can run anywhere.<br/>'
    '• <b>Python</b> — NOT required. n8n runs JavaScript in Code nodes.<br/>'
    '• <b>PostgreSQL / Redis</b> — NOT required. n8n handles its own data storage.<br/>'
    '• <b>Docker</b> — NOT required for n8n cloud users. Self-hosted can use Docker or npm.',
    body_style))
story.append(PageBreak())

# ═══ CHAPTER 2: Workflow Architecture Overview ════════════════════════════
add_heading('2. Workflow Architecture Overview', h1_style, level=0, story=story)

add_heading('2.1 The 3-Stage Pipeline', h2_style, level=1, story=story)
story.append(Paragraph(
    'The workflow implements the same 3-stage institutional pipeline as the Python platform. '
    'Each stage has a strict filtering role — only the highest-quality setups progress to the next stage.', body_style))

pipeline_data = [
    ['Stage', 'Input', 'Output', 'Logic', 'Node'],
    ['Stage 1', '500 pairs', '30 candidates', 'Volume filter, basic math', 'Code: Stage 1 Filter'],
    ['Stage 2', '30 candidates', '5 premium setups', 'Indicators + Smart Money + Confluence', 'Code: Stage 2 Deep Analysis'],
    ['Stage 3', '5 setups', 'BUY/SELL/HOLD/REJECT', 'AI validation (OpenRouter)', 'HTTP Request + Code: Parse AI'],
    ['Final', 'AI-approved BUY/SELL', 'Telegram alert', 'Institutional format message', 'HTTP Request: Telegram'],
]
add_table(pipeline_data, story, col_widths=[1.8 * cm, 2.5 * cm, 3 * cm, 4.5 * cm, 4.5 * cm])

add_heading('2.2 Node Flow Diagram', h2_style, level=1, story=story)
story.append(Paragraph(
    'The workflow contains 12 nodes connected as follows:', body_style))

flow_data = [
    ['Step', 'Node Name', 'Type', 'Purpose'],
    ['1', 'Every Minute', 'Schedule Trigger', 'Starts scan every 60 seconds'],
    ['2', 'Get All Tickers', 'HTTP Request', 'Fetches 24h ticker for all Binance USDT pairs'],
    ['3', 'Stage 1: Filter Top 30', 'Code', 'Filters to top 30 by volume (>$5M)'],
    ['4', 'Stage 2: Deep Analysis', 'Code', 'Fetches 3 timeframes, computes all indicators, filters to confluence ≥70'],
    ['5', 'Has Premium Setups?', 'IF', 'True → continue; False → end cycle'],
    ['6', 'AI Validation Loop', 'Split In Batches', 'Processes each of 5 setups one at a time'],
    ['7', 'OpenRouter AI', 'HTTP Request', 'Calls Gemma 4 26B (free) for institutional validation'],
    ['8', 'Parse AI + Safety Rules', 'Code', 'Parses AI JSON, applies safety overrides'],
    ['9', 'AI Approved BUY/SELL?', 'IF', 'True → send alert; False → skip'],
    ['10', 'Format Telegram Alert', 'Code', 'Builds institutional message template'],
    ['11', 'Send Telegram Alert', 'HTTP Request', 'Sends message to Telegram chat'],
    ['12', 'Skip - No Alert', 'NoOp', 'Setup was filtered — no alert'],
]
add_table(flow_data, story, col_widths=[1.2 * cm, 3.8 * cm, 2.8 * cm, 8.5 * cm])

add_heading('2.3 Data Flow Summary', h2_style, level=1, story=story)
story.append(Paragraph(
    'The data flows through the workflow as follows:', body_style))
story.append(Paragraph(
    '1. <b>Schedule Trigger</b> fires every 60 seconds → passes empty item to HTTP Request.<br/>'
    '2. <b>Get All Tickers</b> returns an array of ~700 ticker objects from Binance.<br/>'
    '3. <b>Stage 1 Code</b> filters to top 30 USDT perpetuals by 24h quote volume, outputs 30 items.<br/>'
    '4. <b>Stage 2 Code</b> fetches 3 timeframes of klines for each, computes indicators, market structure, smart money, confluence. Outputs 0-5 premium setup items.<br/>'
    '5. <b>IF node</b> checks if any setups exist (no_setups field is not true).<br/>'
    '6. <b>Split In Batches</b> iterates over each premium setup one at a time.<br/>'
    '7. <b>OpenRouter HTTP</b> sends the setup context to Gemma 4 26B for AI validation.<br/>'
    '8. <b>Parse AI Code</b> extracts decision/confidence/reasoning from AI response, applies safety rules.<br/>'
    '9. <b>IF node</b> checks if should_alert is true (AI said BUY or SELL).<br/>'
    '10. <b>Format Telegram Code</b> builds the institutional alert message with all fields.<br/>'
    '11. <b>Telegram HTTP</b> sends the formatted message to your Telegram chat.<br/>'
    '12. Loop continues to next setup until all 5 are processed.',
    body_style))
story.append(PageBreak())

# ═══ CHAPTER 3: Import the Workflow JSON ══════════════════════════════════
add_heading('3. Import the Workflow JSON', h1_style, level=0, story=story)

add_heading('3.1 Step-by-Step Import', h2_style, level=1, story=story)
story.append(Paragraph(
    'The workflow JSON file (n8n_workflow.json) contains all 12 nodes, their configurations, '
    'and connections pre-configured. Follow these steps to import it into your n8n instance.', body_style))

steps_data = [
    ['Step', 'Action', 'Expected Result'],
    ['1', 'Open your n8n instance in a browser (e.g. http://localhost:5678 or your n8n cloud URL)', 'n8n dashboard loads'],
    ['2', 'Click "Workflows" in the left sidebar', 'Workflows list appears'],
    ['3', 'Click "Add Workflow" button (top right)', 'Empty workflow canvas opens'],
    ['4', 'Click the three dots menu (top right) → "Import from File"', 'File picker dialog opens'],
    ['5', 'Select n8n_workflow.json from your computer', 'Workflow imports — 12 nodes appear on canvas'],
    ['6', 'Review the workflow — you should see all 12 nodes connected', 'Nodes are laid out left to right'],
    ['7', 'Do NOT activate yet — configure credentials first (Chapter 4)', 'Workflow shows as "Inactive"'],
]
add_table(steps_data, story, col_widths=[1.2 * cm, 8 * cm, 7 * cm])

add_heading('3.2 What You Should See After Import', h2_style, level=1, story=story)
story.append(Paragraph(
    'After successful import, the workflow canvas should display 12 nodes connected in this order:', body_style))
story.append(Paragraph(
    '<b>Every Minute</b> → <b>Get All Tickers</b> → <b>Stage 1: Filter Top 30</b> → '
    '<b>Stage 2: Deep Analysis</b> → <b>Has Premium Setups?</b> → <b>AI Validation Loop</b> → '
    '<b>OpenRouter AI</b> → <b>Parse AI + Safety Rules</b> → <b>AI Approved BUY/SELL?</b> → '
    '<b>Format Telegram Alert</b> → <b>Send Telegram Alert</b>', body_style))

story.append(Paragraph(
    'If any node has a red warning icon, it means credentials are not yet configured. '
    'This is expected — proceed to Chapter 4 to set up credentials.', note_style))

add_heading('3.3 Verifying Node Count', h2_style, level=1, story=story)
story.append(Paragraph(
    'Click anywhere on the canvas (not on a node) to see the workflow summary in the right panel. '
    'It should show:', body_style))
story.append(Paragraph(
    '• <b>Nodes:</b> 12<br/>'
    '• <b>Connections:</b> 12<br/>'
    '• <b>Active:</b> No (you will activate after configuration)',
    body_style))
story.append(PageBreak())

# ═══ CHAPTER 4: Credentials Setup ═════════════════════════════════════════
add_heading('4. Credentials Setup', h1_style, level=0, story=story)

add_heading('4.1 Credential 1: OpenRouter (AI Provider)', h2_style, level=1, story=story)
story.append(Paragraph(
    'OpenRouter provides free access to Gemma 4 26B (and other models). You need an API key '
    'to authenticate HTTP requests to the OpenRouter chat completions endpoint.', body_style))

story.append(Paragraph('<b>How to get your OpenRouter API key:</b>', h3_style))
story.append(Paragraph(
    '1. Go to <b>https://openrouter.ai/</b> in your browser.<br/>'
    '2. Click "Sign In" (top right) → sign in with Google or GitHub.<br/>'
    '3. After login, click your profile (top right) → "Keys".<br/>'
    '4. Click "Create Key" → give it a name (e.g. "n8n crypto platform").<br/>'
    '5. Copy the key — it starts with <code>sk-or-v1-</code>.<br/>'
    '6. Save this key securely — you will not be able to see it again.',
    body_style))

story.append(Paragraph('<b>How to add it to n8n:</b>', h3_style))
story.append(Paragraph(
    '1. In n8n, go to <b>Settings</b> (left sidebar) → <b>Credentials</b>.<br/>'
    '2. Click "Add Credential".<br/>'
    '3. Search for "HTTP Header Auth" and select it.<br/>'
    '4. Fill in the fields:', body_style))

or_cred_data = [
    ['Field', 'Value'],
    ['Name', 'OpenRouter Auth'],
    ['Header Name', 'Authorization'],
    ['Header Value', 'Bearer sk-or-v1-YOUR_ACTUAL_KEY_HERE'],
]
add_table(or_cred_data, story, col_widths=[4 * cm, 12 * cm])

story.append(Paragraph(
    '5. Click "Save". The credential is now available for use in HTTP Request nodes.', body_style))

add_heading('4.2 Credential 2: Telegram Bot Token', h2_style, level=1, story=story)
story.append(Paragraph(
    'Telegram bot token is used to send alerts to your Telegram chat. The token identifies your bot.', body_style))

story.append(Paragraph('<b>How to create a Telegram bot:</b>', h3_style))
story.append(Paragraph(
    '1. Open Telegram and search for <b>@BotFather</b>.<br/>'
    '2. Send <code>/newbot</code> command.<br/>'
    '3. BotFather asks for a bot name → type any name (e.g. "My Crypto Signals").<br/>'
    '4. BotFather asks for a username → type a unique username ending in "bot" (e.g. "my_crypto_signals_bot").<br/>'
    '5. BotFather gives you a token in format: <code>1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ</code>.<br/>'
    '6. Copy this token.',
    body_style))

story.append(Paragraph('<b>How to get your Chat ID:</b>', h3_style))
story.append(Paragraph(
    '1. Open your new bot in Telegram and send <code>/start</code> then send "hello".<br/>'
    '2. Open this URL in your browser (replace TOKEN with your bot token):<br/>'
    '   <code>https://api.telegram.org/botYOUR_TOKEN/getUpdates</code><br/>'
    '3. Look for <code>"chat":{"id":XXXXXXX}</code> in the JSON response.<br/>'
    '4. Copy the number — that is your Chat ID.',
    body_style))

story.append(Paragraph('<b>How to add Telegram credentials to n8n:</b>', h3_style))
story.append(Paragraph(
    'The workflow uses environment variables for Telegram (not n8n credentials). You need to set '
    'two environment variables in n8n:', body_style))

tg_env_data = [
    ['Variable', 'Value', 'Where to Set'],
    ['TELEGRAM_BOT_TOKEN', '1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ', 'n8n Settings → Variables'],
    ['TELEGRAM_CHAT_ID', '8337950513 (your chat ID)', 'n8n Settings → Variables'],
]
add_table(tg_env_data, story, col_widths=[5 * cm, 6 * cm, 5 * cm])

story.append(Paragraph(
    'In n8n cloud: go to Settings → Variables → Add Variable.<br/>'
    'In self-hosted n8n: set these in your docker-compose.yml or .env file as '
    '<code>N8N_ENV_VARS=TELEGRAM_BOT_TOKEN=xxx,TELEGRAM_CHAT_ID=xxx</code> or use environment variables.',
    note_style))

add_heading('4.3 Credential 3: Binance (NOT Required)', h2_style, level=1, story=story)
story.append(Paragraph(
    'The workflow uses only Binance <b>public market data endpoints</b> which do NOT require an API key. '
    'You do not need to create any Binance credential in n8n.', success_style))
story.append(PageBreak())

# ═══ CHAPTER 5: Node 1 — Schedule Trigger ═════════════════════════════════
add_heading('5. Node 1: Schedule Trigger', h1_style, level=0, story=story)

add_heading('5.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'The Schedule Trigger node starts the workflow every 60 seconds. It is the heartbeat of the platform — '
    'every minute, it fires and initiates a new scan cycle.', body_style))

add_heading('5.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value', 'Notes'],
    ['Trigger Interval', 'Minutes', 'How often to fire'],
    ['Minutes Interval', '1', 'Every 1 minute = 60 seconds'],
    ['Hour of the day', '(empty)', 'Leave empty for interval mode'],
]
add_table(config_data, story, col_widths=[4 * cm, 3 * cm, 9 * cm])

add_heading('5.3 How to Change the Scan Frequency', h2_style, level=1, story=story)
story.append(Paragraph(
    'If you want to scan more or less frequently, change the Minutes Interval value:', body_style))
freq_data = [
    ['Frequency', 'Setting', 'Use Case'],
    ['Every 30 seconds', 'Not possible with "Minutes" — use "Seconds" mode, 30', 'Aggressive scanning (higher API usage)'],
    ['Every 1 minute', 'Minutes mode, 1', 'Default — recommended'],
    ['Every 5 minutes', 'Minutes mode, 5', 'Conservative — saves API quota'],
    ['Every 15 minutes', 'Minutes mode, 15', 'Very conservative — minimal AI usage'],
]
add_table(freq_data, story, col_widths=[3.5 * cm, 6 * cm, 6.5 * cm])

add_heading('5.4 Common Mistakes', h2_style, level=1, story=story)
story.append(Paragraph(
    '• <b>Workflow not activating</b> — Make sure the workflow is toggled to "Active" (top right switch).<br/>'
    '• <b>Trigger not firing</b> — n8n cloud requires the workflow to be activated. Self-hosted requires the n8n service to be running.<br/>'
    '• <b>Firing too fast</b> — If you set interval to less than 60 seconds, you may hit Binance rate limits (2400 weight/minute).',
    warn_style))
story.append(PageBreak())

# ═══ CHAPTER 6: Node 2 — Get All Tickers ══════════════════════════════════
add_heading('6. Node 2: HTTP Request — Get All Tickers', h1_style, level=0, story=story)

add_heading('6.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'This node fetches the 24-hour price statistics for ALL Binance USDT Futures pairs in a single API call. '
    'Binance returns an array of ~700 ticker objects. This is the only API call needed for Stage 1 filtering.', body_style))

add_heading('6.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value'],
    ['Method', 'GET'],
    ['URL', 'https://fapi.binance.com/fapi/v1/ticker/24hr'],
    ['Authentication', 'None (public endpoint)'],
    ['Send Headers', 'No'],
    ['Send Body', 'No'],
    ['Timeout', '30000 ms'],
    ['Response Format', 'JSON'],
]
add_table(config_data, story, col_widths=[4 * cm, 12 * cm])

add_heading('6.3 API Details', h2_style, level=1, story=story)
story.append(Paragraph(
    '• <b>Endpoint:</b> /fapi/v1/ticker/24hr<br/>'
    '• <b>Weight:</b> 40 (out of 2400 per minute budget)<br/>'
    '• <b>Rate limit:</b> 60 calls per minute max (we call once per minute)<br/>'
    '• <b>Response:</b> Array of objects, each with symbol, lastPrice, priceChangePercent, volume, quoteVolume, highPrice, lowPrice, count (trade count)',
    body_style))

add_heading('6.4 Response Example (truncated)', h2_style, level=1, story=story)
add_code('''[
  {
    "symbol": "BTCUSDT",
    "priceChangePercent": "2.350",
    "lastPrice": "64150.00",
    "volume": "1134346.12",
    "quoteVolume": "72197879906.51",
    "highPrice": "64800.00",
    "lowPrice": "62300.00",
    "count": 5234671
  },
  {
    "symbol": "ETHUSDT",
    ...
  }
]''', story)

add_heading('6.5 Troubleshooting', h2_style, level=1, story=story)
trouble_data = [
    ['Error', 'Cause', 'Fix'],
    ['HTTP 429', 'Rate limited', 'Increase scan interval to 2+ minutes'],
    ['HTTP 451', 'Binance blocked your region', 'Use a VPN or different server region'],
    ['Timeout', 'Network slow', 'Increase timeout to 60000 ms'],
    ['Empty response', 'Binance API down', 'Check https://status.binance.com'],
]
add_table(trouble_data, story, col_widths=[3.5 * cm, 5 * cm, 7.5 * cm])
story.append(PageBreak())

# ═══ CHAPTER 7: Node 3 — Stage 1 Filter ═══════════════════════════════════
add_heading('7. Node 3: Code — Stage 1 Filter', h1_style, level=0, story=story)

add_heading('7.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'Stage 1 is the fast mathematical scanner. It takes the ~700 tickers from Node 2 and filters them '
    'down to the top 30 candidates based on volume and trade count. No AI, no indicators — just basic math. '
    'This is what makes scanning 500 pairs in under 5 seconds possible.', body_style))

add_heading('7.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value'],
    ['Node Type', 'Code'],
    ['Mode', 'Run Once for All Items'],
    ['Language', 'JavaScript'],
]
add_table(config_data, story, col_widths=[4 * cm, 12 * cm])

add_heading('7.3 JavaScript Code', h2_style, level=1, story=story)
story.append(Paragraph('The complete code is already in the imported workflow. Here is what it does:', body_style))
add_code('''// Filter: USDT perpetuals with sufficient volume
const candidates = tickers
  .filter(t => {
    if (!t.symbol || !t.symbol.endsWith('USDT')) return false;
    const vol = parseFloat(t.quoteVolume || 0);
    const trades = parseInt(t.count || 0);
    return vol >= MIN_VOLUME_USD && trades >= MIN_TRADE_COUNT;
  })
  .map(t => ({ /* extract fields */ }))
  .sort((a, b) => b.quote_volume_24h - a.quote_volume_24h)
  .slice(0, 500);  // max 500 pairs

// Take top 30 by volume for Stage 2
const top30 = candidates.slice(0, TOP_N);
return top30.map(c => ({ json: c }));''', story)

add_heading('7.4 Parameters You Can Change', h2_style, level=1, story=story)
params_data = [
    ['Parameter', 'Default', 'Purpose', 'When to Change'],
    ['MIN_VOLUME_USD', '5000000', 'Minimum 24h volume ($5M)', 'Lower to 1M for more pairs; higher to 10M for fewer'],
    ['MIN_TRADE_COUNT', '100', 'Minimum trade count', 'Lower for low-liquidity pairs'],
    ['TOP_N', '30', 'How many to pass to Stage 2', 'Increase to 50 for more candidates; decrease to 20 for speed'],
]
add_table(params_data, story, col_widths=[4 * cm, 2 * cm, 5 * cm, 5 * cm])

add_heading('7.5 Output Format', h2_style, level=1, story=story)
story.append(Paragraph(
    'Stage 1 outputs 30 items, each with this structure:', body_style))
add_code('''{
  "symbol": "BTCUSDT",
  "price": 64150.00,
  "price_change_pct_24h": 2.35,
  "volume_24h": 1134346.12,
  "quote_volume_24h": 72197879906.51,
  "high_24h": 64800.00,
  "low_24h": 62300.00,
  "trade_count_24h": 5234671
}''', story)
story.append(PageBreak())

# ═══ CHAPTER 8: Node 4 — Stage 2 Deep Analysis ═══════════════════════════
add_heading('8. Node 4: Code — Stage 2 Deep Analysis', h1_style, level=0, story=story)

add_heading('8.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'Stage 2 is the institutional analysis engine. For each of the 30 candidates, it:', body_style))
story.append(Paragraph(
    '1. Fetches 3 timeframes of klines (1h, 15m, 5m) — 200 candles each<br/>'
    '2. Computes technical indicators (EMA 9/21/50, ATR, RSI, ADX, MACD)<br/>'
    '3. Detects market structure (swing highs/lows, BOS, CHOCH)<br/>'
    '4. Analyzes Smart Money Concepts (taker buy/sell ratio, OB mitigation)<br/>'
    '5. Calculates multi-timeframe trend alignment<br/>'
    '6. Computes confluence score 0-100<br/>'
    '7. Calculates risk parameters (entry, SL, TP, RR)<br/>'
    '8. Filters to premium setups only (confluence ≥ 70, RR ≥ 1:2)<br/>'
    '9. Returns top 5 setups by confluence',
    body_style))

add_heading('8.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value'],
    ['Node Type', 'Code'],
    ['Mode', 'Run Once for All Items'],
    ['Language', 'JavaScript (ES6 + async/await)'],
    ['Execution Timeout', '60 seconds (default)'],
]
add_table(config_data, story, col_widths=[4 * cm, 12 * cm])

add_heading('8.3 Key Thresholds (in the code)', h2_style, level=1, story=story)
thresh_data = [
    ['Threshold', 'Default', 'Purpose'],
    ['confluence >= 70', '70', 'Stage 2 minimum confluence to be a premium setup'],
    ['rr >= 2.0', '2.0', 'Minimum risk:reward ratio (1:2)'],
    ['riskPct <= 3.0', '3.0%', 'Maximum stop-loss distance (% of price)'],
    ['htfTrend.adx >= 15', '15', 'Minimum ADX (no trading in no-trend markets)'],
    ['top 5', '5', 'Max setups to send to AI (cost control)'],
]
add_table(thresh_data, story, col_widths=[5 * cm, 2 * cm, 9 * cm])

add_heading('8.4 How to Adjust Thresholds', h2_style, level=1, story=story)
story.append(Paragraph(
    'Open the Stage 2 Code node and find these lines near the bottom:', body_style))
add_code('''// Filter: confluence >= 70
if (confluence < 70) continue;

// Reject if RR < 2.0
if (rr < 2.0) continue;

// Reject if SL too wide (intraday > 3%)
if (riskPct > 3.0) continue;

// Reject if HTF ADX < 15 (no real trend)
if (htfTrend.adx < 15) continue;''', story)

story.append(Paragraph(
    'Change the numbers to adjust filtering strictness. Lower values = more setups (but lower quality). '
    'Higher values = fewer setups (but higher quality).', note_style))

add_heading('8.5 Output Format', h2_style, level=1, story=story)
story.append(Paragraph(
    'Stage 2 outputs 0-5 items (premium setups), each with this structure:', body_style))
add_code('''{
  "symbol": "BTCUSDT",
  "direction": "SELL",
  "price": 64150.00,
  "entry": 64150.00,
  "stop_loss": 65430.00,
  "take_profit": 61590.00,
  "risk_pct": 2.0,
  "reward_pct": 4.0,
  "risk_reward": 2.0,
  "confluence_score": 82,
  "htf_trend": "BEARISH",
  "mtf_trend": "BEARISH",
  "ltf_trend": "BEARISH",
  "htf_adx": 38.5,
  "rsi": 38.45,
  "atr_pct": 1.85,
  "smart_money_flow": -0.63,
  "buy_pct": 0.22,
  "sell_pct": 0.78,
  "volume_spike": 2.35,
  "market_structure_event": "BOS_BEAR",
  "price_change_pct_24h": -2.85,
  "indicators": { "rsi": 38.45, "adx": 38.5, "plus_di": 18.0, "minus_di": 42.0, "atr": 1187.78, "atr_pct": 1.85 }
}''', story)
story.append(PageBreak())

# ═══ CHAPTER 9: Node 5 — IF Premium Setups ════════════════════════════════
add_heading('9. Node 5: IF — Has Premium Setups?', h1_style, level=0, story=story)

add_heading('9.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'This IF node checks whether Stage 2 found any premium setups. If no setups were found '
    '(common in ranging markets), the workflow ends gracefully without calling AI. This saves '
    'AI API costs and avoids unnecessary Telegram alerts.', body_style))

add_heading('9.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value', 'Notes'],
    ['Condition Type', 'Boolean', 'Checks if a value is true/false'],
    ['Left Value', '={{ $json.no_setups }}', 'Field set by Stage 2 when no setups found'],
    ['Operator', 'is not true', 'We want to continue when no_setups is NOT true'],
    ['True branch', '→ AI Validation Loop', 'Premium setups exist — proceed to AI'],
    ['False branch', '(empty / end)', 'No setups — end cycle'],
]
add_table(config_data, story, col_widths=[3.5 * cm, 5.5 * cm, 7 * cm])

add_heading('9.3 How It Works', h2_style, level=1, story=story)
story.append(Paragraph(
    'Stage 2 returns <code>{ no_setups: true, message: "..." }</code> when zero premium setups are found. '
    'The IF node checks if <code>no_setups</code> is true. If it is NOT true (meaning setups exist), '
    'the workflow continues to AI validation. If it IS true, the cycle ends.', body_style))

story.append(Paragraph(
    'This is critical for cost control — calling AI when there are no setups would waste API quota.', body_style))
story.append(PageBreak())

# ═══ CHAPTER 10: Node 6 — Split In Batches ════════════════════════════════
add_heading('10. Node 6: Split In Batches — AI Validation Loop', h1_style, level=0, story=story)

add_heading('10.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'The Split In Batches node processes each premium setup one at a time. This is important because:', body_style))
story.append(Paragraph(
    '1. <b>Rate limiting</b> — OpenRouter free tier has rate limits. Processing sequentially avoids 429 errors.<br/>'
    '2. <b>Cost control</b> — Max 5 AI calls per cycle (one per setup).<br/>'
    '3. <b>Error isolation</b> — If one AI call fails, the others still succeed.',
    body_style))

add_heading('10.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value', 'Notes'],
    ['Batch Size', '1', 'Process one setup at a time'],
    ['Options', '(default)', 'No special options needed'],
]
add_table(config_data, story, col_widths=[4 * cm, 3 * cm, 9 * cm])

add_heading('10.3 How the Loop Works', h2_style, level=1, story=story)
story.append(Paragraph(
    'Split In Batches has two outputs:', body_style))
story.append(Paragraph(
    '• <b>Output 0 (loop)</b> — Sends the current batch item to the next nodes (AI validation). '
    'After the AI validation + Telegram alert completes, the workflow loops back to this node for the next item.<br/>'
    '• <b>Output 1 (done)</b> — Fires when all items have been processed. This ends the cycle.',
    body_style))

story.append(Paragraph(
    'The loop continues until all 5 premium setups have been validated by AI. Each iteration: '
    'fetch AI response → parse → check if alert should be sent → send Telegram (if yes) → loop back.',
    body_style))
story.append(PageBreak())

# ═══ CHAPTER 11: Node 7 — OpenRouter AI ═══════════════════════════════════
add_heading('11. Node 7: HTTP Request — OpenRouter AI', h1_style, level=0, story=story)

add_heading('11.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'This node calls OpenRouter\'s chat completions API to validate the premium setup. The AI receives '
    'the full market context (symbol, direction, confluence, trend, smart money, risk parameters) and '
    'returns a structured JSON decision: BUY, SELL, WATCHLIST, HOLD, or REJECT.', body_style))

add_heading('11.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value'],
    ['Method', 'POST'],
    ['URL', 'https://openrouter.ai/api/v1/chat/completions'],
    ['Authentication', 'Generic Credential Type → HTTP Header Auth'],
    ['Credential', 'OpenRouter Auth (created in Chapter 4)'],
    ['Send Body', 'Yes'],
    ['Body Content Type', 'JSON'],
    ['Timeout', '60000 ms'],
]
add_table(config_data, story, col_widths=[4 * cm, 12 * cm])

add_heading('11.3 Request Body Template', h2_style, level=1, story=story)
story.append(Paragraph(
    'The JSON body sent to OpenRouter contains the system prompt (institutional trader role) and the '
    'user message (market context). The body uses n8n expressions to inject data from the current setup:', body_style))
add_code('''{
  "model": "google/gemma-4-26b-a4b-it:free",
  "messages": [
    {
      "role": "system",
      "content": "You are a senior institutional crypto futures trader..."
    },
    {
      "role": "user",
      "content": "VALIDATE: Symbol: {{$json.symbol}}, Direction: {{$json.direction}},
        Price: {{$json.price}}, Confluence: {{$json.confluence_score}}/100,
        HTF Trend: {{$json.htf_trend}} (ADX {{$json.indicators.adx.toFixed(1)}}),
        Smart Money flow: {{$json.smart_money_flow.toFixed(2)},
        RR: 1:{{$json.risk_reward.toFixed(2)}},
        Entry: {{$json.entry}}, SL: {{$json.stop_loss}}, TP: {{$json.take_profit}}.
        Return JSON only."
    }
  ],
  "temperature": 0.2,
  "max_tokens": 400
}''', story)

add_heading('11.4 How to Change the AI Model', h2_style, level=1, story=story)
story.append(Paragraph(
    'To use a different AI model, change the "model" field in the JSON body:', body_style))
model_data = [
    ['Model', 'Provider', 'Cost', 'Notes'],
    ['google/gemma-4-26b-a4b-it:free', 'OpenRouter', 'Free', 'Default — recommended'],
    ['meta-llama/llama-3.3-70b-instruct:free', 'OpenRouter', 'Free', 'Better reasoning, may be rate-limited'],
    ['openai/gpt-4o-mini', 'OpenAI', 'Paid', 'Very reliable, fast'],
    ['anthropic/claude-3.5-sonnet', 'OpenRouter', 'Paid', 'Best reasoning, expensive'],
    ['groq/llama-3.1-70b-versatile', 'Groq', 'Free', 'Fast, but may have regional blocks'],
]
add_table(model_data, story, col_widths=[6 * cm, 2.5 * cm, 1.5 * cm, 6 * cm])

add_heading('11.5 OpenRouter Response Example', h2_style, level=1, story=story)
add_code('''{
  "choices": [{
    "message": {
      "content": "{\\"decision\\": \\"SELL\\", \\"confidence\\": 0.85, \\"probability\\": 0.78, \\"trade_quality\\": \\"A\\", \\"risk_level\\": \\"MEDIUM\\", \\"reasoning\\": \\"Strong bearish confluence...\\"}"
    }
  }]
}''', story)
story.append(PageBreak())

# ═══ CHAPTER 12: Node 8 — Parse AI + Safety ═══════════════════════════════
add_heading('12. Node 8: Code — Parse AI + Apply Safety Rules', h1_style, level=0, story=story)

add_heading('12.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'This node does two things:', body_style))
story.append(Paragraph(
    '1. <b>Parses the AI response</b> — extracts decision, confidence, reasoning from the JSON content.<br/>'
    '2. <b>Applies safety overrides</b> — enforces institutional rules from the spec. Even if AI says BUY, '
    'the safety rules can downgrade to HOLD or REJECT if conditions are not met.',
    body_style))

add_heading('12.2 Safety Rules Applied', h2_style, level=1, story=story)
safety_data = [
    ['#', 'Rule', 'Condition', 'Override'],
    ['1', 'Confluence gate', 'confluence < 75', 'BUY/SELL → HOLD'],
    ['2', 'HTF agreement', 'HTF disagrees with direction', 'BUY/SELL → HOLD'],
    ['3', 'Smart money confirmation', 'smart_money_flow < 0.15 (BUY) or > -0.15 (SELL)', 'BUY/SELL → WATCHLIST'],
    ['4', 'Risk/Reward gate', 'risk_reward < 2.0', 'BUY/SELL → REJECT'],
]
add_table(safety_data, story, col_widths=[1 * cm, 3.5 * cm, 6.5 * cm, 5 * cm])

add_heading('12.3 should_alert Logic', h2_style, level=1, story=story)
story.append(Paragraph(
    'After all safety rules are applied, the code sets <code>should_alert = true</code> only if the final '
    'decision is BUY or SELL. This is the field that the next IF node checks.', body_style))
add_code('''const shouldAlert = (decision === 'BUY' || decision === 'SELL');''', story)

story.append(Paragraph(
    'This ensures that HOLD, WATCHLIST, and REJECT decisions never trigger a Telegram alert. '
    'Only actionable BUY/SELL signals reach your Telegram.', success_style))
story.append(PageBreak())

# ═══ CHAPTER 13: Node 9 — IF AI Approved ══════════════════════════════════
add_heading('13. Node 9: IF — AI Approved BUY/SELL?', h1_style, level=0, story=story)

add_heading('13.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'This IF node is the final gate before sending a Telegram alert. It checks the <code>should_alert</code> '
    'field set by Node 8. If true (AI approved BUY or SELL), the workflow continues to format and send '
    'the Telegram message. If false, the setup is skipped (routed to NoOp).', body_style))

add_heading('13.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value', 'Notes'],
    ['Condition Type', 'Boolean', 'Checks should_alert field'],
    ['Left Value', '={{ $json.should_alert }}', 'Set by Parse AI node'],
    ['Operator', 'is true', 'True = AI approved BUY/SELL'],
    ['True branch', '→ Format Telegram Alert', 'Send alert'],
    ['False branch', '→ Skip - No Alert (NoOp)', 'Don\'t send alert'],
]
add_table(config_data, story, col_widths=[3.5 * cm, 5.5 * cm, 7 * cm])

add_heading('13.3 Why This Matters', h2_style, level=1, story=story)
story.append(Paragraph(
    'This node enforces the "precision over quantity" principle from the spec. Most cycles will have '
    '0 alerts because the market conditions don\'t meet all the strict criteria. This is intentional — '
    'institutional traders don\'t trade every setup.', body_style))

story.append(Paragraph(
    'In a ranging market, you may go 24+ hours without alerts. This is normal and expected. '
    'When a trending market emerges, you will get 1-3 high-quality alerts per day.', note_style))
story.append(PageBreak())

# ═══ CHAPTER 14: Node 10 — Format Telegram ════════════════════════════════
add_heading('14. Node 10: Code — Format Telegram Alert', h1_style, level=0, story=story)

add_heading('14.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'This node builds the institutional alert message in HTML format. The message includes:', body_style))
story.append(Paragraph(
    '• <b>Header</b> with coin, direction bias, signal type<br/>'
    '• <b>Priority + Quality</b> with star rating and AI confidence<br/>'
    '• <b>Trade Plan</b> with entry, stop loss, TP1/TP2/TP3, risk/reward<br/>'
    '• <b>Market Structure</b> with multi-timeframe trends, ADX, RSI<br/>'
    '• <b>Market Data</b> with buy/sell pressure, 24h change, ATR<br/>'
    '• <b>AI Analysis</b> with reasoning (trimmed to 2 sentences)<br/>'
    '• <b>Trade Invalidation</b> with dynamic rules per direction<br/>'
    '• <b>Manual Checklist</b> with 6 items<br/>'
    '• <b>Final Verdict</b> with decision and disclaimer',
    body_style))

add_heading('14.2 Output Format', h2_style, level=1, story=story)
story.append(Paragraph('The code outputs a single item with:', body_style))
add_code('''{
  "chat_id": "8337950513",
  "text": "━━━━━━━━━━━━━━━━━━━━\\n🏛 Institutional AI Market Intelligence\\n...",
  "parse_mode": "HTML",
  "disable_web_page_preview": true
}''', story)

add_heading('14.3 How to Customize the Alert', h2_style, level=1, story=story)
story.append(Paragraph(
    'Open the Format Telegram Alert Code node. The message is built as an array of lines joined with '
    '\\n. You can add, remove, or modify any line. Common customizations:', body_style))

custom_data = [
    ['What to Change', 'Where in Code', 'How'],
    ['Remove a section', 'Find the section array', 'Delete the lines for that section'],
    ['Add emoji to direction', 'dirEmoji object', 'Change the emoji values'],
    ['Change disclaimer text', 'Bottom of message array', 'Edit the italic text lines'],
    ['Add more TP levels', 'After TP3 calculation', 'Add tp4 = entry + riskDist * 4, etc.'],
    ['Change symbol emoji', 'symEmoji ternary', 'Add more conditions for other coins'],
]
add_table(custom_data, story, col_widths=[4 * cm, 5 * cm, 7 * cm])
story.append(PageBreak())

# ═══ CHAPTER 15: Node 11 — Send Telegram ══════════════════════════════════
add_heading('15. Node 11: HTTP Request — Send Telegram Alert', h1_style, level=0, story=story)

add_heading('15.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'This node sends the formatted alert message to your Telegram chat via the Telegram Bot API.', body_style))

add_heading('15.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value'],
    ['Method', 'POST'],
    ['URL', '=https://api.telegram.org/bot{{$env.TELEGRAM_BOT_TOKEN}}/sendMessage'],
    ['Authentication', 'None (token is in the URL)'],
    ['Send Body', 'Yes'],
    ['Body Content Type', 'JSON'],
    ['JSON Body', '={{ JSON.stringify($json) }}'],
    ['Timeout', '15000 ms'],
]
add_table(config_data, story, col_widths=[4 * cm, 12 * cm])

add_heading('15.3 How the URL Works', h2_style, level=1, story=story)
story.append(Paragraph(
    'The URL uses an n8n expression <code>{{$env.TELEGRAM_BOT_TOKEN}}</code> which reads the '
    'TELEGRAM_BOT_TOKEN environment variable. This is set in n8n Settings → Variables (see Chapter 4).', body_style))

story.append(Paragraph(
    'The JSON body is built by the previous Code node (Format Telegram Alert) and contains '
    'chat_id, text, parse_mode, and disable_web_page_preview. The <code>{{ JSON.stringify($json) }}</code> '
    'expression converts the input item to a JSON string for the request body.', body_style))

add_heading('15.4 Telegram API Response', h2_style, level=1, story=story)
story.append(Paragraph('On success, Telegram returns:', body_style))
add_code('''{
  "ok": true,
  "result": {
    "message_id": 12345,
    "from": { "username": "your_bot" },
    "chat": { "id": 8337950513 },
    "date": 1721308800,
    "text": "━━━━━━━━━━━━━━━━━━━━..."
  }
}''', story)
story.append(PageBreak())

# ═══ CHAPTER 16: Node 12 — NoOp ═══════════════════════════════════════════
add_heading('16. Node 12: NoOp — Skip (No Alert)', h1_style, level=0, story=story)

add_heading('16.1 Purpose', h2_style, level=1, story=story)
story.append(Paragraph(
    'The NoOp (No Operation) node is a placeholder for setups that were filtered out by the AI safety rules '
    '(HOLD, WATCHLIST, or REJECT). It does nothing — the setup is simply skipped, and the workflow loops '
    'back to process the next premium setup.', body_style))

add_heading('16.2 Configuration', h2_style, level=1, story=story)
config_data = [
    ['Field', 'Value'],
    ['Node Type', 'NoOp'],
    ['Parameters', '(none)'],
    ['Notes', 'Add a note like "Setup filtered — no alert sent"'],
]
add_table(config_data, story, col_widths=[4 * cm, 12 * cm])

add_heading('16.3 After NoOp', h2_style, level=1, story=story)
story.append(Paragraph(
    'After the NoOp, the workflow connects back to the "AI Validation Loop" (Split In Batches) node. '
    'This tells n8n to process the next setup in the batch. When all setups are processed, the loop '
    'ends and the cycle is complete.', body_style))
story.append(PageBreak())

# ═══ CHAPTER 17: Testing & Verification ═══════════════════════════════════
add_heading('17. Testing & Verification', h1_style, level=0, story=story)

add_heading('17.1 Manual Trigger Test', h2_style, level=1, story=story)
story.append(Paragraph(
    'To test the workflow without waiting for the schedule trigger:', body_style))
story.append(Paragraph(
    '1. Open the workflow in n8n.<br/>'
    '2. Click the "Every Minute" (Schedule Trigger) node.<br/>'
    '3. Click "Execute Workflow" button (top of canvas).<br/>'
    '4. Watch the data flow through each node — green checkmarks = success, red = error.<br/>'
    '5. Click any node to see its input/output data.',
    body_style))

add_heading('17.2 Viewing Execution History', h2_style, level=1, story=story)
story.append(Paragraph(
    'Every workflow run is logged. To view past executions:', body_style))
story.append(Paragraph(
    '1. Click "Executions" in the left sidebar.<br/>'
    '2. You will see a list of all runs with timestamp, status (success/error), and duration.<br/>'
    '3. Click any execution to see the full data flow — every node\'s input and output.<br/>'
    '4. This is invaluable for debugging.',
    body_style))

add_heading('17.3 Verifying Telegram Test', h2_style, level=1, story=story)
story.append(Paragraph(
    'To verify that Telegram alerts work, you can use the Telegram API directly:', body_style))
add_code('''curl -s -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/sendMessage" \\
  -H "Content-Type: application/json" \\
  -d '{
    "chat_id": "YOUR_CHAT_ID",
    "text": "🚀 Test alert from n8n workflow setup!",
    "parse_mode": "HTML"
  }' ''', story)

story.append(Paragraph(
    'If you receive the test message in Telegram, your bot token and chat ID are correct. '
    'If not, double-check Chapter 4 credentials setup.', note_style))

add_heading('17.4 Common Test Scenarios', h2_style, level=1, story=story)
test_data = [
    ['Scenario', 'Expected Result', 'If Different'],
    ['Execute workflow manually', 'All nodes turn green', 'Check red node\'s error message'],
    ['Check execution history', 'See successful runs every 60s', 'If no runs, workflow not activated'],
    ['Check Telegram', 'Receive alert when premium setup found', 'May be 0 alerts in ranging market — normal'],
    ['Check OpenRouter dashboard', 'See API calls in usage log', 'If no calls, workflow not reaching AI node'],
]
add_table(test_data, story, col_widths=[4 * cm, 5.5 * cm, 6.5 * cm])
story.append(PageBreak())

# ═══ CHAPTER 18: Troubleshooting ══════════════════════════════════════════
add_heading('18. Troubleshooting Guide', h1_style, level=0, story=story)

add_heading('18.1 Common Errors and Fixes', h2_style, level=1, story=story)
trouble_data = [
    ['Error', 'Cause', 'Fix'],
    ['HTTP 429 from Binance', 'Rate limited (too many API calls)', 'Increase scan interval to 2+ minutes in Schedule Trigger'],
    ['HTTP 429 from OpenRouter', 'Free model rate limited', 'Switch to a different free model or use paid model'],
    ['HTTP 401 from OpenRouter', 'Invalid API key', 'Re-check OpenRouter Auth credential — must be "Bearer sk-or-v1-..."'],
    ['HTTP 403 from OpenRouter', 'Region blocked or key invalid', 'Try a VPN or get a new API key from openrouter.ai'],
    ['Telegram 400 chat not found', 'Wrong chat ID or bot never started', 'Send /start to your bot first, then re-check chat ID'],
    ['Telegram 401 unauthorized', 'Wrong bot token', 'Re-create bot token via @BotFather'],
    ['Stage 2 returns 0 setups', 'Market ranging (low ATR) or all filtered', 'Normal in ranging markets — wait for trending conditions'],
    ['AI returns non-JSON', 'Model didn\'t follow instructions', 'Parse AI code has fallback — check it extracted a decision'],
    ['Workflow not firing', 'Not activated', 'Toggle workflow to "Active" (top right switch)'],
    ['Code node timeout', 'Stage 2 taking >60s for 30 symbols', 'Reduce TOP_N from 30 to 15 in Stage 1 code'],
    ['Split In Batches stuck', 'Loop not completing', 'Check that Send Telegram node connects back to AI Validation Loop'],
]
add_table(trouble_data, story, col_widths=[4 * cm, 4.5 * cm, 7.5 * cm])

add_heading('18.2 Debugging Tips', h2_style, level=1, story=story)
story.append(Paragraph(
    '1. <b>Use the Executions tab</b> — Click any execution to see exactly which node failed and what data it received.<br/>'
    '2. <b>Add console.log statements</b> — In Code nodes, add <code>console.log(`debug: ${JSON.stringify(item)}`)</code> '
    'to see data in the node output panel.<br/>'
    '3. <b>Test individual nodes</b> — Click a node, then click "Execute Node" to run just that node with its current input.<br/>'
    '4. <b>Check n8n logs</b> — Self-hosted: <code>docker logs n8n</code>. Cloud: contact support.<br/>'
    '5. <b>Verify credentials</b> — Settings → Credentials → click "Test connection" on each credential.',
    body_style))
story.append(PageBreak())

# ═══ CHAPTER 19: Tuning & Customization ═══════════════════════════════════
add_heading('19. Tuning & Customization', h1_style, level=0, story=story)

add_heading('19.1 Adjusting Scan Frequency', h2_style, level=1, story=story)
story.append(Paragraph(
    'In the "Every Minute" (Schedule Trigger) node, change the Minutes Interval:', body_style))
story.append(Paragraph(
    '• <b>1 minute</b> (default) — Most aggressive, highest API usage<br/>'
    '• <b>2-5 minutes</b> — Balanced, recommended for production<br/>'
    '• <b>15 minutes</b> — Conservative, minimal API usage',
    body_style))

add_heading('19.2 Adjusting Confluence Threshold', h2_style, level=1, story=story)
story.append(Paragraph(
    'In the "Stage 2: Deep Analysis" Code node, find the line:', body_style))
add_code('''if (confluence < 70) continue;''', story)
story.append(Paragraph(
    '• <b>70</b> (default) — Strict, premium setups only<br/>'
    '• <b>60</b> — More setups, medium quality<br/>'
    '• <b>80</b> — Very strict, only the best setups',
    body_style))

add_heading('19.3 Adjusting AI Provider', h2_style, level=1, story=story)
story.append(Paragraph(
    'In the "OpenRouter AI" HTTP Request node, change the "model" field in the JSON body:', body_style))
add_code('"model": "google/gemma-4-26b-a4b-it:free"', story)
story.append(Paragraph(
    'Options: <code>meta-llama/llama-3.3-70b-instruct:free</code>, <code>openai/gpt-4o-mini</code>, '
    '<code>anthropic/claude-3.5-sonnet</code>, etc. See OpenRouter models page for full list.', body_style))

add_heading('19.4 Adjusting Telegram Alert Format', h2_style, level=1, story=story)
story.append(Paragraph(
    'In the "Format Telegram Alert" Code node, modify the message array. You can:', body_style))
story.append(Paragraph(
    '• Add/remove sections<br/>'
    '• Change emojis<br/>'
    '• Add more TP levels<br/>'
    '• Change disclaimer text<br/>'
    '• Add custom fields from the setup data',
    body_style))

add_heading('19.5 Adjusting Number of Pairs Scanned', h2_style, level=1, story=story)
story.append(Paragraph(
    'In the "Stage 1: Filter Top 30" Code node, change the TOP_N value:', body_style))
add_code('''const TOP_N = 30;  // Change to 50, 20, or 100''', story)

add_heading('19.6 Switching to Groq (if OpenRouter is blocked)', h2_style, level=1, story=story)
story.append(Paragraph(
    '1. Get a Groq API key from https://console.groq.com<br/>'
    '2. Update the OpenRouter Auth credential (or create a new "Groq Auth" credential)<br/>'
    '3. In the OpenRouter AI node, change:<br/>'
    '   • URL: <code>https://api.groq.com/openai/v1/chat/completions</code><br/>'
    '   • Model: <code>llama-3.1-70b-versatile</code>',
    body_style))
story.append(PageBreak())

# ═══ CHAPTER 20: Production Deployment ════════════════════════════════════
add_heading('20. Production Deployment', h1_style, level=0, story=story)

add_heading('20.1 n8n Cloud (Easiest)', h2_style, level=1, story=story)
story.append(Paragraph(
    'n8n cloud is the simplest option — no server management, automatic updates, built-in monitoring.', body_style))
story.append(Paragraph(
    '1. Sign up at https://n8n.cloud<br/>'
    '2. Choose a plan (Starter plan is sufficient for this workflow)<br/>'
    '3. Import the workflow JSON<br/>'
    '4. Configure credentials<br/>'
    '5. Activate the workflow<br/>'
    '6. n8n runs 24/7 — no server needed',
    body_style))

add_heading('20.2 n8n Self-Hosted on AWS EC2', h2_style, level=1, story=story)
story.append(Paragraph(
    'For full control and no per-execution costs, self-host n8n on AWS EC2:', body_style))
story.append(Paragraph(
    '1. Launch an EC2 instance (t3.small recommended, $15/month)<br/>'
    '2. Install Docker + Docker Compose<br/>'
    '3. Create docker-compose.yml with n8n + PostgreSQL<br/>'
    '4. Start with <code>docker compose up -d</code><br/>'
    '5. Access n8n at http://YOUR_EC2_IP:5678<br/>'
    '6. Import workflow + configure credentials<br/>'
    '7. Activate workflow',
    body_style))

story.append(Paragraph('<b>Sample docker-compose.yml for n8n:</b>', h3_style))
add_code('''version: '3.8'
services:
  n8n:
    image: n8nio/n8n:latest
    ports: ["5678:5678"]
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=your_password
      - TELEGRAM_BOT_TOKEN=your_telegram_bot_token
      - TELEGRAM_CHAT_ID=your_chat_id
    volumes:
      - n8n_data:/home/node/.n8n
    restart: unless-stopped

volumes:
  n8n_data:''', story)

add_heading('20.3 Backup Your Workflow', h2_style, level=1, story=story)
story.append(Paragraph(
    'To backup your workflow (with all customizations):', body_style))
story.append(Paragraph(
    '1. Open the workflow in n8n<br/>'
    '2. Click three dots menu (top right) → "Download"<br/>'
    '3. Save the JSON file to a safe location<br/>'
    '4. This file contains all your customizations (thresholds, credentials references, etc.)',
    body_style))

add_heading('20.4 Cost Analysis', h2_style, level=1, story=story)
cost_data = [
    ['Option', 'Monthly Cost', 'Pros', 'Cons'],
    ['n8n Cloud (Starter)', '$20/month', 'No management, auto-updates', 'Per-execution limits'],
    ['AWS EC2 t3.small', '$15/month', 'Full control, unlimited executions', 'You manage the server'],
    ['AWS EC2 t3.micro (free tier)', '$0 (1 year)', 'Free for first year', 'Limited RAM (1GB)'],
    ['Local computer', '$0', 'Free', 'Not 24/7 unless always on'],
]
add_table(cost_data, story, col_widths=[4 * cm, 2.5 * cm, 4.5 * cm, 5 * cm])
story.append(PageBreak())

# ═══ APPENDIX A: Complete Code Snippets ═══════════════════════════════════
add_heading('21. Appendix A: Complete Code Snippets', h1_style, level=0, story=story)

story.append(Paragraph(
    'All JavaScript code from all Code nodes is included in the imported workflow JSON. '
    'This appendix provides the code in a copy-paste-friendly format for reference or customization.', body_style))

add_heading('21.1 Stage 1 Filter Code', h2_style, level=1, story=story)
story.append(Paragraph(
    'See Node 3 (Chapter 7) for the complete Stage 1 code. Key parameters to customize: '
    'MIN_VOLUME_USD, MIN_TRADE_COUNT, TOP_N.', body_style))

add_heading('21.2 Stage 2 Deep Analysis Code', h2_style, level=1, story=story)
story.append(Paragraph(
    'See Node 4 (Chapter 8) for the complete Stage 2 code. This is the largest code block — it '
    'includes all indicator functions (EMA, ATR, RSI, ADX), market structure detection, confluence '
    'scoring, and risk calculation. Key thresholds: confluence >= 70, rr >= 2.0, riskPct <= 3.0, '
    'htfTrend.adx >= 15.', body_style))

add_heading('21.3 Parse AI + Safety Rules Code', h2_style, level=1, story=story)
story.append(Paragraph(
    'See Node 8 (Chapter 12) for the complete AI parsing + safety code. The four safety rules '
    'are clearly marked and can be adjusted independently.', body_style))

add_heading('21.4 Format Telegram Alert Code', h2_style, level=1, story=story)
story.append(Paragraph(
    'See Node 10 (Chapter 14) for the complete Telegram formatting code. The message is built '
    'as an array of lines — easy to customize by adding or removing lines.', body_style))
story.append(PageBreak())

# ═══ APPENDIX B: Binance API Reference ════════════════════════════════════
add_heading('22. Appendix B: Binance API Reference', h1_style, level=0, story=story)

add_heading('22.1 Endpoints Used', h2_style, level=1, story=story)
api_data = [
    ['Endpoint', 'Method', 'Weight', 'Purpose', 'Auth'],
    ['/fapi/v1/ticker/24hr', 'GET', '40', '24h ticker for all pairs', 'None'],
    ['/fapi/v1/klines', 'GET', '2 (limit≤500)', 'Candlestick data', 'None'],
    ['/fapi/v1/exchangeInfo', 'GET', '1', 'Exchange info (symbols)', 'None'],
    ['/fapi/v1/fundingRate', 'GET', '1', 'Funding rate history', 'None'],
    ['/fapi/v1/openInterest', 'GET', '1', 'Current open interest', 'None'],
]
add_table(api_data, story, col_widths=[4 * cm, 1.5 * cm, 2.5 * cm, 5.5 * cm, 2.5 * cm])

add_heading('22.2 Rate Limits', h2_style, level=1, story=story)
story.append(Paragraph(
    'Binance Futures API rate limits:', body_style))
story.append(Paragraph(
    '• <b>Weight limit:</b> 2400 per minute (IP-based)<br/>'
    '• <b>Order limit:</b> 300 per 10 seconds, 100,000 per day<br/>'
    '• <b>Our usage:</b> ~42 weight per cycle (1 ticker + 90 klines for 30 symbols × 3 timeframes)<br/>'
    '• <b>Headroom:</b> We use ~42 out of 2400 per minute — well within limits',
    body_style))

add_heading('22.3 Base URLs', h2_style, level=1, story=story)
url_data = [
    ['Environment', 'REST Base URL', 'WebSocket Base URL'],
    ['Mainnet', 'https://fapi.binance.com/fapi', 'wss://fstream.binance.com'],
    ['Testnet', 'https://testnet.binancefuture.com/fapi', 'wss://stream.binancefuture.com'],
]
add_table(url_data, story, col_widths=[3 * cm, 6.5 * cm, 6.5 * cm])
story.append(PageBreak())

# ═══ APPENDIX C: AI Prompt Templates ══════════════════════════════════════
add_heading('23. Appendix C: AI Prompt Templates', h1_style, level=0, story=story)

add_heading('23.1 System Prompt (Institutional Trader Role)', h2_style, level=1, story=story)
add_code('''You are a senior institutional crypto futures trader. Validate ONLY premium setups.
Respond ONLY with valid JSON:
{
  "decision": "BUY|SELL|WATCHLIST|HOLD|REJECT",
  "confidence": 0.0-1.0,
  "probability": 0.0-1.0,
  "trade_quality": "A|B|C",
  "risk_level": "LOW|MEDIUM|HIGH",
  "reasoning": "one paragraph"
}

Safety Rules (MANDATORY):
- If confluence < 75 → HOLD
- If higher timeframe disagrees → HOLD
- If smart money confirmation missing → WATCHLIST
- If risk/reward < 1:2 → REJECT
- Prefer REJECT over mediocre trades.''', story)

add_heading('23.2 User Prompt Template (with Variables)', h2_style, level=1, story=story)
add_code('''VALIDATE THIS PREMIUM SETUP:
Symbol: {{$json.symbol}}
Direction: {{$json.direction}}
Price: {{$json.price}}
Confluence: {{$json.confluence_score}}/100
HTF Trend: {{$json.htf_trend}} (ADX {{$json.indicators.adx.toFixed(1)}})
15M Trend: {{$json.mtf_trend}}
5M Trend: {{$json.ltf_trend}}
Smart Money flow: {{$json.smart_money_flow.toFixed(2)}}
RSI: {{$json.indicators.rsi.toFixed(1)}}
Risk/Reward: 1:{{$json.risk_reward.toFixed(2)}}
Entry: {{$json.entry}}
Stop Loss: {{$json.stop_loss}}
Take Profit: {{$json.take_profit}}
Market Structure: {{$json.market_structure_event}}
24h Change: {{$json.price_change_pct_24h}}%

Return JSON only.''', story)

add_heading('23.3 Switching AI Providers', h2_style, level=1, story=story)
provider_data = [
    ['Provider', 'Base URL', 'Model Name', 'Auth Header'],
    ['OpenRouter', 'https://openrouter.ai/api/v1', 'google/gemma-4-26b-a4b-it:free', 'Bearer sk-or-v1-...'],
    ['Groq', 'https://api.groq.com/openai/v1', 'llama-3.1-70b-versatile', 'Bearer gsk_...'],
    ['NVIDIA NIM', 'https://integrate.api.nvidia.com/v1', 'meta/llama-3.1-70b-instruct', 'Bearer nvapi-...'],
    ['OpenAI', 'https://api.openai.com/v1', 'gpt-4o-mini', 'Bearer sk-...'],
]
add_table(provider_data, story, col_widths=[3 * cm, 5.5 * cm, 5 * cm, 4.5 * cm])

story.append(Paragraph(
    'All providers use the OpenAI-compatible chat completions API format. To switch, change the URL '
    'and model in the "OpenRouter AI" HTTP Request node, and update the credential.', body_style))

# Final page
story.append(PageBreak())
story.append(Spacer(1, 5 * cm))
story.append(Paragraph('Guide Complete', title_style))
story.append(Spacer(1, 1 * cm))
story.append(Paragraph(
    'You now have everything needed to run the Institutional Crypto Futures Intelligence Platform '
    'in n8n. Import the workflow JSON, configure the 2 credentials (OpenRouter + Telegram), '
    'activate the workflow, and wait for premium setups.', body_style))
story.append(Spacer(1, 2 * cm))
story.append(HRFlowable(width='80%', thickness=2, color=HEADER_FILL))
story.append(Spacer(1, 1 * cm))
story.append(Paragraph(
    '<para align="center"><i>For support, check the troubleshooting chapter (18) or '
    'review n8n execution history for detailed error logs.</i></para>',
    ParagraphStyle('Footer', fontSize=10, textColor=TEXT_MUTED, alignment=TA_CENTER)))


# ─── Build the PDF ──────────────────────────────────────────────────────────
doc = TocDocTemplate(
    str(OUTPUT),
    pagesize=A4,
    leftMargin=2 * cm,
    rightMargin=2 * cm,
    topMargin=2 * cm,
    bottomMargin=2 * cm,
    title='n8n Workflow Setup Guide — Institutional Crypto Futures Intelligence Platform',
    author='Z.ai',
    subject='n8n workflow configuration guide',
    creator='Z.ai PDF Generator',
)

doc.multiBuild(story)
print(f"✅ PDF guide written to: {OUTPUT}")
print(f"   File size: {OUTPUT.stat().st_size:,} bytes")
