# -*- coding: utf-8 -*-
import os
import json
import functools
from flask import Flask, request, render_template, redirect, url_for, flash, session, g, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import OperationalError
from werkzeug.security import generate_password_hash, check_password_hash
import mercadopago

# --- Configuração do App ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'uma-chave-secreta-muito-dificil-de-adivinhar'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'criaconecta.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Configuração do Mercado Pago (com o seu Access Token) ---
app.config['MERCADO_PAGO_ACCESS_TOKEN'] = "APP_USR-8393374766693490-061720-40d23212ed1800b38905de55ec0562d5-1578097267"

db = SQLAlchemy(app)
sdk = mercadopago.SDK(app.config['MERCADO_PAGO_ACCESS_TOKEN'])


# ==============================================================================
# MÓDULO: Models
# ==============================================================================
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)
    tipo = db.Column(db.String(50), nullable=False, default='criador') # 'criador' ou 'empresa'
    plano = db.Column(db.String(50), nullable=False, default='gratuito') # 'gratuito' ou 'pro'

    def set_password(self, senha): self.senha_hash = generate_password_hash(senha)
    def check_password(self, senha): return check_password_hash(self.senha_hash, senha)

class Briefing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    orcamento = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(50), default='pagamento_pendente')
    id_empresa = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    criador_contratado_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    empresa = db.relationship('Usuario', foreign_keys=[id_empresa], backref='briefings_criados')
    criador_contratado = db.relationship('Usuario', foreign_keys=[criador_contratado_id])

# ==============================================================================
# MÓDULO: Autenticação
# ==============================================================================
def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Você precisa estar logado para aceder a esta página.", "error")
            return redirect(url_for('login'))
        return view(**kwargs)
    return wrapped_view

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    try:
        g.user = db.session.get(Usuario, user_id) if user_id else None
    except OperationalError:
        g.user = None

# ==============================================================================
# MÓDULO: Rotas
# ==============================================================================
@app.route("/")
def home():
    try:
        vagas = Briefing.query.filter_by(status='pagamento_pendente').order_by(Briefing.id.desc()).all()
    except OperationalError:
        vagas = []
    return render_template('index.html', vagas=vagas)

@app.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        usuario = Usuario.query.filter_by(email=request.form['email']).first()
        if usuario is None or not usuario.check_password(request.form['senha']):
            flash('Email ou senha incorretos.', 'error')
            return redirect(url_for('login'))
        session.clear()
        session['user_id'] = usuario.id
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Você foi desconectado.', 'message')
    return redirect(url_for('home'))

@app.route("/dashboard")
@login_required
def dashboard():
    if g.user.tipo == 'empresa':
        jobs_pagamento_pendente = Briefing.query.filter_by(id_empresa=g.user.id, status='pagamento_pendente').all()
        jobs_em_andamento = Briefing.query.filter_by(id_empresa=g.user.id, status='em_andamento').all()
        return render_template('dashboard_empresa.html', jobs_pagamento_pendente=jobs_pagamento_pendente, jobs_em_andamento=jobs_em_andamento)
    else:
        return "Dashboard do Criador"

@app.route("/criar-vaga", methods=['GET', 'POST'])
@login_required
def criar_vaga():
    if g.user.tipo != 'empresa': return redirect(url_for('home'))
    if request.method == 'POST':
        novo_briefing = Briefing(
            titulo=request.form['titulo'],
            orcamento=float(request.form.get('orcamento') or 0),
            id_empresa=g.user.id,
            criador_contratado_id=2 # Simulação de contratação do criador com ID 2
        )
        db.session.add(novo_briefing)
        db.session.commit()
        flash('Briefing criado! Realize o pagamento para iniciar o job.', 'message')
        return redirect(url_for('pagamento', vaga_id=novo_briefing.id))
    return render_template('criar-vaga.html')
    
@app.route('/perfil/<int:user_id>')
def perfil(user_id):
    usuario = db.session.get(Usuario, user_id)
    return render_template('perfil.html', usuario=usuario)

@app.route('/job/<int:vaga_id>/pagamento')
@login_required
def pagamento(vaga_id):
    vaga = db.session.get(Briefing, vaga_id)
    if not vaga or g.user.id != vaga.id_empresa: return redirect(url_for('home'))
    
    preference_data = {
        "items": [{"title": f"Job: {vaga.titulo}", "quantity": 1, "unit_price": vaga.orcamento}],
        "back_urls": {
            "success": url_for('pagamento_feedback', _external=True),
            "failure": url_for('pagamento_feedback', _external=True),
        },
        "auto_return": "approved",
        "external_reference": str(vaga.id)
    }
    try:
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response["response"]
        return render_template('pagamento.html', vaga=vaga, checkout_url=preference["init_point"])
    except Exception as e:
        print(f"Erro no Mercado Pago: {e}")
        flash("Erro ao comunicar com o sistema de pagamento.", "error")
        return redirect(url_for('dashboard'))

@app.route('/pagamento/feedback')
def pagamento_feedback():
    status = request.args.get('status')
    vaga_id = request.args.get('external_reference')
    if status == 'approved' and vaga_id:
        vaga = db.session.get(Briefing, int(vaga_id))
        if vaga:
            vaga.status = 'em_andamento'
            db.session.commit()
            flash('Pagamento aprovado! O seu job já está em andamento.', 'message')
        return redirect(url_for('dashboard'))
    flash('O pagamento falhou ou foi cancelado.', 'error')
    return redirect(url_for('dashboard'))

# ==============================================================================
# MÓDULO: Comandos de Terminal
# ==============================================================================
@app.cli.command('init-db')
def init_db_command():
    try:
        db.drop_all()
        db.create_all()
        empresa = Usuario(nome='Boutique Chique', email='empresa@email.com', tipo='empresa')
        empresa.set_password('123')
        criador = Usuario(nome='Ana Culinária', email='criador@email.com', tipo='criador')
        criador.set_password('123')
        db.session.add_all([empresa, criador])
        db.session.commit()
        vaga = Briefing(titulo="Campanha de Dia das Mães", orcamento=750.50, id_empresa=empresa.id, criador_contratado_id=criador.id)
        db.session.add(vaga)
        db.session.commit()
        print('Banco de dados inicializado com sucesso.')
    except Exception as e:
        print(f"Erro ao inicializar: {e}")

if __name__ == '__main__':
    app.run(debug=True)
