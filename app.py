"""
Férias Mil Grau — Sistema de Inscrição v3 (Python/Flask)
Backend completo com SQLite, upload de arquivos e geração de PDF.
"""

import os, random, string, json
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, jsonify,
                   redirect, url_for, session, send_from_directory,
                   send_file, abort)
from werkzeug.utils import secure_filename

# ── PDF ──────────────────────────────────────────────────────────────────────
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

# ── Banco de dados ────────────────────────────────────────────────────────────
import sqlite3

DB_PATH = "ferias.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS participantes (
            id          TEXT PRIMARY KEY,
            nome        TEXT NOT NULL,
            email       TEXT NOT NULL,
            telefone    TEXT NOT NULL,
            idade       TEXT,
            cidade      TEXT,
            quarto_id   TEXT,
            quarto_nome TEXT,
            dias        TEXT,
            checkin     TEXT DEFAULT 'Não',
            status      TEXT DEFAULT 'Confirmado',
            data        TEXT
        );

        CREATE TABLE IF NOT EXISTS documentos (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id           TEXT NOT NULL,
            doc_participante    TEXT DEFAULT '',
            doc_responsavel     TEXT DEFAULT '',
            autorizacao         TEXT DEFAULT '',
            atestado            TEXT DEFAULT '',
            comunicacao         TEXT DEFAULT '',
            FOREIGN KEY (ticket_id) REFERENCES participantes(id)
        );
        """)

# ── Quartos ───────────────────────────────────────────────────────────────────
QUARTOS_CAPACIDADE = 8

def build_quartos():
    quartos = []
    for n in range(1, 25):
        if n <= 4:   genero, grupo, cor = 'Meninas', 'Laranja', 'laranja'
        elif n <= 8:  genero, grupo, cor = 'Meninas', 'Roxo',    'roxo'
        elif n <= 12: genero, grupo, cor = 'Meninas', 'Verde',   'verde'
        elif n <= 16: genero, grupo, cor = 'Meninos', 'Laranja', 'laranja'
        elif n <= 20: genero, grupo, cor = 'Meninos', 'Roxo',    'roxo'
        else:         genero, grupo, cor = 'Meninos', 'Verde',   'verde'
        quartos.append(dict(
            id=f"Q{n:02d}", num=n, genero=genero,
            grupo=grupo, cor=cor, capacidade=QUARTOS_CAPACIDADE
        ))
    return quartos

def get_quartos_com_vagas():
    quartos = build_quartos()
    with get_db() as db:
        rows = db.execute(
            "SELECT quarto_id, COUNT(*) as total FROM participantes "
            "WHERE status != 'Cancelado' AND quarto_id IS NOT NULL "
            "GROUP BY quarto_id"
        ).fetchall()
    ocupados = {r['quarto_id']: r['total'] for r in rows}
    for q in quartos:
        q['ocupados'] = ocupados.get(q['id'], 0)
        q['vagas'] = q['capacidade'] - q['ocupados']
    return quartos

# ── Helpers ───────────────────────────────────────────────────────────────────
ALLOWED_EXT = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def gen_id(existing_ids=None):
    chars = string.ascii_uppercase + string.digits
    while True:
        tid = 'FMG-' + ''.join(random.choices(chars, k=6))
        if existing_ids is None or tid not in existing_ids:
            return tid

def format_br(dt: datetime):
    return dt.strftime('%d/%m/%Y %H:%M')

# ── App ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fmg-secret-2026')

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
PDF_FOLDER    = os.path.join(os.path.dirname(__file__), 'static', 'pdf')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PDF_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB

# ── Auth ──────────────────────────────────────────────────────────────────────
ADMIN_USER = 'admin'
ADMIN_PASS = 'admin123'

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ═══════════════════════════════════════════════════════════════════════════════
#  ROTAS — INSCRIÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('inscricao.html')

@app.route('/api/quartos')
def api_quartos():
    return jsonify(get_quartos_com_vagas())

@app.route('/api/inscricao', methods=['POST'])
def api_inscricao():
    data = request.get_json(force=True)
    nome     = (data.get('nome') or '').strip()
    email    = (data.get('email') or '').strip()
    telefone = (data.get('telefone') or '').strip()
    if not nome or not email or not telefone:
        return jsonify(error='Campos obrigatórios ausentes'), 400

    quarto_id = data.get('quarto_id', '').strip()
    quartos   = get_quartos_com_vagas()
    q_info    = next((q for q in quartos if q['id'] == quarto_id), None)
    if not q_info:
        return jsonify(error='Quarto inválido'), 400
    if q_info['vagas'] <= 0:
        return jsonify(error='Quarto sem vagas'), 409

    with get_db() as db:
        ids = [r['id'] for r in db.execute("SELECT id FROM participantes").fetchall()]
        tid = gen_id(ids)
        qNome = f"Quarto {q_info['num']} — {q_info['genero']} {q_info['grupo']}"
        dias  = data.get('dias', 'Sexta 01/08, Sábado 02/08, Domingo 03/08')
        now   = format_br(datetime.now())
        db.execute(
            "INSERT INTO participantes (id,nome,email,telefone,idade,cidade,"
            "quarto_id,quarto_nome,dias,checkin,status,data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, nome, email, telefone,
             data.get('idade',''), data.get('cidade',''),
             quarto_id, qNome, dias, 'Não', 'Confirmado', now)
        )
        db.execute(
            "INSERT INTO documentos (ticket_id) VALUES (?)", (tid,)
        )
    return jsonify(id=tid, quarto_nome=qNome), 201

@app.route('/api/anexo/upload', methods=['POST'])
def api_upload():
    ticket_id = request.form.get('ticket_id', '').strip()
    tipo      = request.form.get('tipo', '').strip()
    if not ticket_id or not tipo:
        return jsonify(error='Parâmetros ausentes'), 400

    TIPOS_VALIDOS = ('doc_participante','doc_responsavel','autorizacao','atestado','comunicacao')
    if tipo not in TIPOS_VALIDOS:
        return jsonify(error='Tipo inválido'), 400

    f = request.files.get('arquivo')
    if not f or not allowed_file(f.filename):
        return jsonify(error='Arquivo inválido'), 400

    ext      = f.filename.rsplit('.', 1)[1].lower()
    filename = secure_filename(f"{ticket_id}_{tipo}.{ext}")
    f.save(os.path.join(UPLOAD_FOLDER, filename))

    with get_db() as db:
        exists = db.execute("SELECT id FROM documentos WHERE ticket_id=?", (ticket_id,)).fetchone()
        if exists:
            db.execute(f"UPDATE documentos SET {tipo}=? WHERE ticket_id=?", (filename, ticket_id))
        else:
            db.execute(f"INSERT INTO documentos (ticket_id, {tipo}) VALUES (?,?)", (ticket_id, filename))

    return jsonify(ok=True, filename=filename)

@app.route('/api/pdf/<ticket_id>')
def api_pdf(ticket_id):
    if not REPORTLAB_OK:
        return jsonify(error='ReportLab não instalado. pip install reportlab'), 501

    with get_db() as db:
        p = db.execute("SELECT * FROM participantes WHERE id=?", (ticket_id,)).fetchone()
    if not p:
        abort(404)

    pdf_path = os.path.join(PDF_FOLDER, f"{ticket_id}.pdf")
    _gerar_pdf(dict(p), pdf_path)
    return send_file(pdf_path, as_attachment=True,
                     download_name=f"inscricao_{ticket_id}.pdf",
                     mimetype='application/pdf')

def _gerar_pdf(p, path):
    doc  = SimpleDocTemplate(path, pagesize=A4,
                             leftMargin=2*cm, rightMargin=2*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('title', fontSize=28, fontName='Helvetica-Bold',
                                 alignment=TA_CENTER, textColor=colors.HexColor('#0066CC'))
    sub_style   = ParagraphStyle('sub', fontSize=12, fontName='Helvetica',
                                 alignment=TA_CENTER, textColor=colors.HexColor('#7AADCC'),
                                 spaceAfter=20)

    elems = [
        Paragraph("FÉRIAS MIL GRAU", title_style),
        Paragraph("Comprovante de Inscrição", sub_style),
        Spacer(1, 0.5*cm),
    ]

    data = [
        ['Campo', 'Informação'],
        ['Código', p['id']],
        ['Nome', p['nome']],
        ['E-mail', p['email']],
        ['Telefone', p['telefone']],
        ['Idade', p.get('idade') or '—'],
        ['Cidade', p.get('cidade') or '—'],
        ['Quarto', p.get('quarto_nome') or '—'],
        ['Dias', p.get('dias') or '—'],
        ['Status', p.get('status') or 'Confirmado'],
        ['Data de inscrição', p.get('data') or '—'],
    ]

    t = Table(data, colWidths=[5*cm, 11*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0066CC')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,0), 11),
        ('ROWBACKGROUNDS', (0,1), (-1,-1),
         [colors.HexColor('#0D1B2A'), colors.HexColor('#0A0F1E')]),
        ('TEXTCOLOR',  (0,1), (-1,-1), colors.HexColor('#F0F8FF')),
        ('FONTNAME',   (0,1), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,1), (-1,-1), 10),
        ('GRID',       (0,0), (-1,-1), 0.5, colors.HexColor('#1A3A5C')),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('ROWHEIGHT',  (0,0), (-1,-1), 0.8*cm),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 1*cm))
    elems.append(Paragraph(
        f"<i>Apresente este comprovante (ou o QR Code) no check-in do evento.</i>",
        ParagraphStyle('note', fontSize=9, textColor=colors.grey, alignment=TA_CENTER)
    ))
    doc.build(elems)

# ═══════════════════════════════════════════════════════════════════════════════
#  ROTAS — ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if (request.form.get('usuario') == ADMIN_USER and
                request.form.get('senha') == ADMIN_PASS):
            session['admin_logged'] = True
            return redirect(url_for('admin_dashboard'))
        error = 'Usuário ou senha incorretos.'
    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    return render_template('admin.html')

# ── API admin ─────────────────────────────────────────────────────────────────

@app.route('/api/admin/stats')
@login_required
def api_admin_stats():
    with get_db() as db:
        total   = db.execute("SELECT COUNT(*) FROM participantes WHERE status!='Cancelado'").fetchone()[0]
        checkin = db.execute("SELECT COUNT(*) FROM participantes WHERE checkin='Sim'").fetchone()[0]
        quartos = get_quartos_com_vagas()
    ocupados = sum(q['ocupados'] for q in quartos)
    lotados  = sum(1 for q in quartos if q['vagas'] <= 0)
    with get_db() as db:
        rows = db.execute("SELECT ticket_id FROM documentos").fetchall()
        tids = {r['ticket_id'] for r in rows}
        com_docs = db.execute(
            "SELECT COUNT(*) FROM participantes WHERE id IN ({}) AND status!='Cancelado'".format(
                ','.join('?' for _ in tids) if tids else "''"
            ), list(tids) if tids else []
        ).fetchone()[0]

    return jsonify(total=total, checkin=checkin, ocupados=ocupados,
                   lotados=lotados, com_docs=com_docs,
                   quartos=[dict(q) for q in quartos])

@app.route('/api/admin/participantes')
@login_required
def api_admin_participantes():
    q     = request.args.get('q', '').lower()
    status = request.args.get('status', '')
    sql   = "SELECT p.*, d.doc_participante, d.doc_responsavel, d.autorizacao, d.atestado, d.comunicacao FROM participantes p LEFT JOIN documentos d ON p.id=d.ticket_id WHERE 1=1"
    params = []
    if q:
        sql += " AND (LOWER(p.nome) LIKE ? OR LOWER(p.email) LIKE ? OR LOWER(p.id) LIKE ? OR LOWER(p.quarto_nome) LIKE ?)"
        params += [f'%{q}%'] * 4
    if status == 'checkin':
        sql += " AND p.checkin='Sim'"
    elif status:
        sql += " AND p.status=?"
        params.append(status)
    sql += " ORDER BY p.data DESC"
    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['docs'] = {
            'doc_participante': d.pop('doc_participante', '') or '',
            'doc_responsavel':  d.pop('doc_responsavel', '') or '',
            'autorizacao':      d.pop('autorizacao', '') or '',
            'atestado':         d.pop('atestado', '') or '',
            'comunicacao':      d.pop('comunicacao', '') or '',
        }
        result.append(d)
    return jsonify(result)

@app.route('/api/admin/participante', methods=['POST'])
@login_required
def api_admin_novo():
    data = request.get_json(force=True)
    nome     = (data.get('nome') or '').strip()
    email    = (data.get('email') or '').strip()
    telefone = (data.get('telefone') or '').strip()
    quarto_id = data.get('quarto_id', '').strip()
    if not nome or not email or not telefone or not quarto_id:
        return jsonify(error='Campos obrigatórios ausentes'), 400

    quartos = get_quartos_com_vagas()
    q_info  = next((q for q in quartos if q['id'] == quarto_id), None)
    if not q_info:
        return jsonify(error='Quarto inválido'), 400

    with get_db() as db:
        ids = [r['id'] for r in db.execute("SELECT id FROM participantes").fetchall()]
        tid = gen_id(ids)
        qNome = f"Quarto {q_info['num']} — {q_info['genero']} {q_info['grupo']}"
        DIAS = {'sex': 'Sexta 01/08', 'sab': 'Sábado 02/08', 'dom': 'Domingo 03/08'}
        dias_raw = data.get('dias', [])
        dias_str = ', '.join(DIAS[d] for d in dias_raw if d in DIAS) or '—'
        now = format_br(datetime.now())
        db.execute(
            "INSERT INTO participantes (id,nome,email,telefone,idade,cidade,"
            "quarto_id,quarto_nome,dias,checkin,status,data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, nome, email, telefone,
             data.get('idade',''), data.get('cidade',''),
             quarto_id, qNome, dias_str, 'Não', 'Confirmado', now)
        )
        db.execute("INSERT INTO documentos (ticket_id) VALUES (?)", (tid,))
    return jsonify(id=tid, quarto_nome=qNome), 201

@app.route('/api/admin/participante/<tid>', methods=['PUT'])
@login_required
def api_admin_editar(tid):
    data = request.get_json(force=True)
    quartos  = get_quartos_com_vagas()
    quarto_id = data.get('quarto_id', '').strip()
    q_info   = next((q for q in quartos if q['id'] == quarto_id), None)
    qNome    = f"Quarto {q_info['num']} — {q_info['genero']} {q_info['grupo']}" if q_info else '—'
    DIAS = {'sex': 'Sexta 01/08', 'sab': 'Sábado 02/08', 'dom': 'Domingo 03/08'}
    dias_str = ', '.join(DIAS[d] for d in data.get('dias', []) if d in DIAS) or data.get('dias_str', '—')
    with get_db() as db:
        db.execute(
            "UPDATE participantes SET nome=?,email=?,telefone=?,idade=?,cidade=?,"
            "quarto_id=?,quarto_nome=?,dias=?,status=? WHERE id=?",
            (data.get('nome',''), data.get('email',''), data.get('telefone',''),
             data.get('idade',''), data.get('cidade',''),
             quarto_id, qNome, dias_str, data.get('status','Confirmado'), tid)
        )
    return jsonify(ok=True)

@app.route('/api/admin/participante/<tid>/cancelar', methods=['POST'])
@login_required
def api_admin_cancelar(tid):
    with get_db() as db:
        db.execute("UPDATE participantes SET status='Cancelado' WHERE id=?", (tid,))
    return jsonify(ok=True)

@app.route('/api/admin/participante/<tid>', methods=['DELETE'])
@login_required
def api_admin_excluir(tid):
    with get_db() as db:
        db.execute("DELETE FROM participantes WHERE id=?", (tid,))
        db.execute("DELETE FROM documentos WHERE ticket_id=?", (tid,))
    return jsonify(ok=True)

@app.route('/api/admin/checkin', methods=['POST'])
@login_required
def api_admin_checkin():
    tid = (request.get_json(force=True).get('id') or '').strip().upper()
    with get_db() as db:
        p = db.execute("SELECT * FROM participantes WHERE id=?", (tid,)).fetchone()
        if not p:
            return jsonify(error='not_found'), 404
        p = dict(p)
        if p['checkin'] == 'Sim':
            return jsonify(error='already_checked', nome=p['nome']), 409
        if p['status'] == 'Cancelado':
            return jsonify(error='cancelled'), 410
        db.execute("UPDATE participantes SET checkin='Sim' WHERE id=?", (tid,))
    return jsonify(ok=True, nome=p['nome'], quarto_nome=p['quarto_nome'])

@app.route('/api/admin/exportar-csv')
@login_required
def api_exportar_csv():
    import csv, io
    with get_db() as db:
        rows = db.execute(
            "SELECT p.*, d.doc_participante, d.doc_responsavel, d.autorizacao, d.atestado, d.comunicacao "
            "FROM participantes p LEFT JOIN documentos d ON p.id=d.ticket_id ORDER BY p.data DESC"
        ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID','Nome','Email','Telefone','Idade','Cidade',
                     'Quarto','Dias','Check-in','Status','Data',
                     'Doc.Participante','Doc.Responsável','Autorização','Atestado','Comunicação'])
    for r in rows:
        r = dict(r)
        writer.writerow([
            r['id'], r['nome'], r['email'], r['telefone'],
            r.get('idade',''), r.get('cidade',''),
            r.get('quarto_nome',''), r.get('dias',''),
            r.get('checkin',''), r.get('status',''), r.get('data',''),
            r.get('doc_participante',''), r.get('doc_responsavel',''),
            r.get('autorizacao',''), r.get('atestado',''), r.get('comunicacao',''),
        ])
    output.seek(0)
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=inscritos_fmg.csv'}
    )

@app.route('/uploads/<filename>')
@login_required
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    print("✅  Férias Mil Grau — rodando em http://localhost:5000")
    print("🔐  Admin: http://localhost:5000/admin  |  admin / admin123")
    app.run(debug=True, port=5000)
