#!/usr/bin/python
# -*- coding: utf-8 -*-

# Código inicial em único script para facilitar desenvolvimento


# Necessário?
import os, sys
reload(sys)
sys.setdefaultencoding('utf-8')


import os
from flask import Flask, render_template, session, redirect, url_for, flash
from flask_script import Manager, Shell
from flask_wtf import Form
from wtforms import FloatField, SubmitField
from flask_bootstrap import Bootstrap
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from flask_migrate import Migrate, MigrateCommand
from flask import Blueprint
from flask import jsonify, request
from flask_socketio import SocketIO, emit, join_room, leave_room, close_room, rooms, disconnect
from sqlalchemy import extract
from flask_moment import Moment
from flask_mail import Mail, Message

from threading import Thread
from celery import Celery
from celery.schedules import crontab

'''
No datasheet do sensor diz que: Flow rate characteristics: 4.1Q +- 10% (erro)
Sendo que Q = L/min
Então temos (1000/246) mL/pulsos =~ 4,07 mL/pulsos
'''
cte = 4.06504065  # mL/pulso
PM = 3.85         # R$/m³ [Preço médio do m³ de água]



basedir = os.path.abspath(os.path.dirname(__file__))

# Set this variable to "threading", "eventlet" or "gevent" to test the
# different async modes, or leave it set to None for the application to choose
# the best option based on installed packages.
async_mode = None

app = Flask(__name__)
api = Blueprint('api', __name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') # Cross-Site Request Forgery (CSRF) protection key 
                                                       # (usado para proteger webforms)
socketio = SocketIO(app, async_mode=async_mode)
thread = None

# Configurando Database
app.config['SQLALCHEMY_DATABASE_URI'] = \
    'sqlite:///' + os.path.join(basedir, 'data.sqlite') # Usando db SQLite para um desenvolvimento simples
app.config['SQLALCHEMY_COMMIT_ON_TEARDOWN'] = True      # Permite commits automáticos das mudanças do db após 
                                                        # o final de cada request
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False    # ao iniciar o shell foi pedido para desativá-lo se não for usado


# Configurando e-mail
app.config['MAIL_SERVER'] = 'smtp.googlemail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_SUBJECT_PREFIX'] = '[UORER] '
app.config['MAIL_SENDER'] = 'UORER <uorer.adm@gmail.com>'
app.config['MAIL_ADMIN'] = os.environ.get('MAIL_ADMIN')

# Configurando o Celery
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['result_backend'] = 'redis://localhost:6379/0'


# Setado por causa do bucho causado pelo url_for('index', _external=True) no email
# app.config['SERVER_NAME'] = os.environ.get('SERVER_NAME')
    

celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)



db = SQLAlchemy(app)
db.text_factory = unicode
manager = Manager(app)
bootstrap = Bootstrap(app)
migrate = Migrate(app,db)
manager.add_command('db', MigrateCommand)
moment = Moment(app)
mail = Mail(app)


def make_shell_context():
    return dict(app = app, db = db, Usuario = Usuario, Medidor = Medidor, Meta = Meta, Medicao = Medicao, 
                meta=Meta.query.first(),
                eu=Usuario.query.first())

manager.add_command("shell", Shell(make_context = make_shell_context))


@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    '''
    # Calls test('olá') every 10 seconds.
    sender.add_periodic_task(10.0, test.s('olá'), name='add every 10')
    '''
    # Executa 'relatorioDiario' todo dias as 20h (UTC)
    sender.add_periodic_task(
        crontab(hour=20, minute=0),
        relatorioDiario.s(),
    )

    # Executa 'desnotificarMensal' todo dia 10 do mês as 0h (UTC)
    sender.add_periodic_task(
        crontab(0, 0, day_of_month='10'),
        desnotificarMensal.s(),
    )


@celery.task
def test(arg):
    print(arg)


@celery.task
def desnotificarMensal():
    with app.app_context():
        usuarios = Usuario.query.all()
        for usuario in usuarios:
            meta = usuario.medidores.first().meta

            # Limpando a notificação da meta diária
            meta.desnotificar("mês", "100%")
            meta.desnotificar("mês", "80%")


@celery.task
def relatorioDiario():
    with app.app_context():
        usuarios = Usuario.query.all()
        for usuario in usuarios:
            meta = usuario.medidores.first().meta

            agora = datetime.utcnow()
            inicioMes = meta.inicio

            while inicioMes < agora:
                inicioMes += meta.intervalo
            inicioMes -= meta.intervalo

            inicioMetaMensal = "{:02d}/{:02d} as {:02d}:{:02d}".format(inicioMes.day, inicioMes.month, inicioMes.hour, inicioMes.minute)



            '''        CONSUMO ACUMULADO NO DIA E NO MÊS         '''
            ultimaMedicao = Medicao.query.order_by(Medicao.id.desc()).first() # pegando o último valor
            
            # última medição do mês passado
            ultimaMedicaoMesPassado = \
                Medicao.query.filter(Medicao.dataHora <= inicioMes).order_by(Medicao.id.desc()).first()
            ultimaMedicaoMesPassado = 0 if(ultimaMedicaoMesPassado is None) else ultimaMedicaoMesPassado.valor
            
            consumoMes = (ultimaMedicao.valor - ultimaMedicaoMesPassado)*cte/1000000 # m³
            
            # consumo do dia em m³
            ontem = datetime(agora.year, agora.month, agora.day, inicioMes.hour, inicioMes.minute)
            ultimaMedicaoOntem = \
                Medicao.query.filter(Medicao.dataHora <= ontem).order_by(Medicao.id.desc()).first()

            ultimaMedicaoOntem = 0 if(ultimaMedicaoOntem is None) else ultimaMedicaoOntem.valor # caso não exista consumo nos dias anteriores
            
            consumoDia = (ultimaMedicao.valor - ultimaMedicaoOntem)*cte/1000000 # m³, atenção porque ultimaMedicaoOntem já é um float, 
                                                                                #     não um objeto Medicao

            # consumos em m³, L e R$
            consumoMes = {
                 "m³": consumoMes
                ,"L":  consumoMes*1000
                ,"R$": consumoMes*PM
            }
            consumoDia = {
                 "m³": consumoDia
                ,"L":  consumoDia*1000
                ,"R$": consumoDia*PM
            }

            consumoMes["%"] = consumoMes[meta.unidadeDoValor.encode('utf-8')]/meta.valor*100
            consumoDia["%"] = consumoDia[meta.unidadeDoValor.encode('utf-8')]/(meta.valor/30)*100 # supondo mês com 30 dias


            sendAsyncEmail.delay({ #(para, assunto, template, **kwargs):
                 "para":     usuario.email
                ,"assunto":  'Relatório diário'
                ,"template": 'email/relatorioDiario'
                ,"kwargs": {
                     "usuario":          usuario.nome
                    ,"horaMeta":         meta.inicio.hour - 3 # conversão porca para o horário brasileiro
                    ,"inicioMetaMensal": inicioMetaMensal
                    ,"consumo": {
                         "dia": consumoDia
                        ,"mês": consumoMes
                    }
                }
                })

            # Limpando a notificação da meta diária
            meta.desnotificar("dia", "100%")
            meta.desnotificar("dia", "80%")



@celery.task
def sendAsyncEmail(parametros): #(para, assunto, template, **kwargs):
    with app.app_context():
        msg = Message(app.config['MAIL_SUBJECT_PREFIX'] + parametros["assunto"],
                      sender=app.config['MAIL_SENDER'], recipients=[parametros["para"]])
        msg.body = render_template(parametros["template"] + '.txt', **parametros["kwargs"])
        msg.html = render_template(parametros["template"] + '.html', **parametros["kwargs"])
        mail.send(msg)




def analisarMetaNotificar(meta):
    if meta.foiNotificado("dia","100%") and meta.foiNotificado("mês", "100%"):
        pass # fazer nada, o usuário já foi notificado pela ultrapassagem dos 100% diário e mensal
    else:
        '''        CONSUMO ACUMULADO NO DIA E NO MÊS         '''
        # Consumo do mês em m³
        ultimaMedicao = Medicao.query.order_by(Medicao.id.desc()).first()
        consumoMes = ultimaMedicao.valor*cte/1000000 # m³

        # Consumo do dia em m³
        # Primeiro pega-se a última medição do dia anterior. No caso utilizamos '<=' para o caso que o medidor não tenha consumo no
        # dia anterior
        agora = datetime.utcnow()
        ultimaMedicaoOntem = \
            Medicao.query.filter(Medicao.dataHora <= datetime(agora.year, agora.month, agora.day)).order_by(Medicao.id.desc()).first()
        consumoDia = ultimaMedicaoOntem.valor if (ultimaMedicaoOntem is not None) else 0 # caso não exista consumo nos dias anteriores
        consumoDia = consumoMes - consumoDia*cte/1000000 # m³

        # consumos em m³, L e R$
        consumoMes = {
             "m³": consumoMes
            ,"L":  consumoMes*1000
            ,"R$": consumoMes*PM
        }
        consumoDia = {
             "m³": consumoDia
            ,"L":  consumoDia*1000
            ,"R$": consumoDia*PM
        }

        # dicionário final
        consumoDiaMes = {
             "dia": consumoDia
            ,"mês": consumoMes
        }        


        # Testar se a meta mensal ou diária está perto ou foi ultrapassada para notificar o usuário
        consumidoPorcento ={
             "dia": consumoDiaMes["dia"]["m³"]/(meta.valor/30)*100    # supondo mês com 30 dias
            ,"mês": consumoDiaMes["mês"]["m³"]/meta.valor*100
        }

        
        for key in consumidoPorcento:
            # Análise dos 100%
            if meta.foiNotificado(key, "100%"):
                pass # fazer nada, o usuário já foi notificado pela passagem de 100% da meta 'key' (diária ou mensal)
            elif consumidoPorcento[key] >= 100:
                meta.notificar(key, "100%") # alteração do db e e-mail

            # Análise dos 80% (note que só será enviado um único e-mail para a meta diária e outro para meta mensal)
            elif meta.foiNotificado(key, "80%"):
                pass # faz nada, o usuário já foi notificado pela ultrapassagem dos 80%
            elif consumidoPorcento[key] >= 80:
                meta.notificar(key, "80%") # alteração do db e e-mail
               


###############################################################################################################################
'''        Modelos do DB        '''

#from .exceptions import ValidationError

### Definição de Permissões dos Usuários ####
# Permissões são definidas com um byte, tendo um dos bits igual a 1 e
# os outros iguais a zero. O nível da permissão é baseado na posição do
# seu bit igual a 1, e aumenta da direita para a esquerda.
# A permissão de "ADMINISTRAR" é especial e tem todos os bits iguais a 1.
class Permissao:
    VISUALIZAR = 0x01
    CADASTRAR = 0x02
    ADMINISTRAR = 0xff


class Cargo(db.Model):    # Tipos de usuários no sistema
    __tablename__ = 'cargos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(30), index=True, unique=True, nullable=False)
    
    # Permissões dos usuários do cargo (interpretado como um byte, em que cada
    # bit representa uma permissão, e o estado do bit indica se o usuário tem (1) ou
    # não (0) tal permissão)
    permissoes = db.Column(db.Integer, nullable=False)

    # Relação de usuários que possuem o cargo    
    usuarios = db.relationship('Usuario', backref='cargo', lazy='dynamic')

    ### Métodos ###

    # Adicionar os cargos no banco de dados
    @staticmethod
    def criar_cargos():
        # Definição dos cargos e suas permissões
        cargos = {
            'Agregado': Permissao.VISUALIZAR
           ,'Proprietário': Permissao.VISUALIZAR
           ,'Consultor': Permissao.CADASTRAR
           ,'Desenvolvedor': Permissao.ADMINISTRAR
           ,'Administrador': Permissao.ADMINISTRAR
        }

        # Criação dos cargos
        for nome_cargo in cargos:
            cargo = Cargo.query.filter_by(nome=nome_cargo).first()

            if cargo is None:
                cargo = Cargo(nome=nome_cargo)
                cargo.permissoes = cargos[nome_cargo]
                db.session.add(cargo)

        # Salvando no banco de dados
        db.session.commit()

    # Representação no shell
    def __repr__(self):
        return '<Cargo: %s>' % self.nome

    # Representação na interface
    def __str__(self):
        return self.nome



# Tabela de associação de usuários proprietários e agregados
hierarquia = db.Table('hierarquia',
    db.Column('proprietarioID', db.Integer, db.ForeignKey('usuarios.id')),
    db.Column('agregadoID', db.Integer, db.ForeignKey('usuarios.id'))
)


class Usuario(db.Model):
    __tablename__ = 'usuarios' # nome da tabela no banco de dados
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(64), index=True)
    endereco = db.Column(db.String(128))
    email = db.Column(db.String(64), index=True, unique=True, nullable=False)
    # Hash da senha (a senha original não é armazenada)
    senhaHash = db.Column(db.String(128))
    # Indica se o usuário confirmou o email
    confirmado = db.Column(db.Boolean, default=False)
    
    # Relacionamento de proprietários e agregados da tabela 'usuarios'
    # Usuários agregados são aqueles em que um proprietário concede o privilégio de visualização de seus medidores
    agregados = db.relationship('Usuario',
                                secondary=hierarquia,
                                primaryjoin=(hierarquia.c.proprietarioID == id),
                                secondaryjoin=(hierarquia.c.agregadoID == id),
                                backref=db.backref('proprietarios', lazy='dynamic'),
                                lazy='dynamic')
    # proprietarios.all() = [todos os proprietarios que tem como agregado o usuário atual]


    # Relação com a tabela 'cargos'
    cargoID = db.Column(db.Integer, db.ForeignKey('cargos.id'))

    # Relação de medidores de um usuário
    medidores = db.relationship('Medidor', backref='usuario', lazy='dynamic')


    ### Métodos ###

    # Criar o primeiro administrador, caso ainda não haja um
    @staticmethod
    def criar_administrador():
        if not Usuario.query.join(Cargo).filter(Cargo.nome=='Administrador').first():
            # Definição dos dados (alguns obtidos em variáveis de ambiente)
            administrador = Usuario(email=current_app.config['ADMIN_EMAIL'])
            administrador.nome = 'Administrador'
            administrador.senha = current_app.config['ADMIN_SENHA']
            administrador.verificado = True
            administrador.confirmado = True
            administrador.cargo = Cargo.query.filter_by(nome='Administrador').first()

            # Salvando no banco de dados
            db.session.add(administrador)
            db.session.commit()


    # Representação no shell
    def __repr__(self):
        return '<Usuário: %s>' % self.nome #[%s]>' % (self.nome, self.cargo.nome)

    # Representação na interface
    def __str__(self):
        return self.nome



class ModeloMedidor(db.Model):
    __tablename__ = 'modelos_dos_medidores'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(32), index=True)

    # Relação de medidores de um modelo
    medidores = db.relationship('Medidor', backref='modelo', lazy='dynamic')


    ### Métodos ###
    # Adicionar os modelos ao banco de dados
    @staticmethod
    def criar_modelos():
        # Definição dos modelos e suas permissões
        modelos = [
            u'1.0'
           ,u'1.1'
           ,u'2.0'
        ]

        # Criação dos modelos
        for nome_modelo in modelos:
            modelo = ModeloMedidor.query.filter_by(nome=nome_modelo).first()
            if modelo is None:
                modelo = ModeloMedidor(nome=nome_modelo)
                db.session.add(modelo)
        # Salvando no banco de dados
        db.session.commit()

    # Representação no shell
    def __repr__(self):
        return '<Modelo de medidor: %s>' % self.nome

    # Representação na interface
    def __str__(self):
        return self.nome


# Tabela de associação de medidores pais e filhos
genealogia = db.Table('genealogia',
    db.Column('paiID', db.Integer, db.ForeignKey('medidores.id')),
    db.Column('filhoID', db.Integer, db.ForeignKey('medidores.id'))
)


class Medidor(db.Model):
    __tablename__ = 'medidores'
    id = db.Column(db.Integer, primary_key=True)
    # Nome para facilitar a identificação do medidor, pode ser o complemento da frase "Medidor do(a) ..."
    nome = db.Column(db.String(64), index=True)
    # Preço médio do m³ de água em R$
    precoMedio = db.Column(db.Float)
    # Constante do medidor que informa quantos m³/pulso ele mede
    cte = db.Column(db.Float)
    # Um endereço para o medidor, já que este pode ser diferente do pertencente ao proprietário
    endereco = db.Column(db.String(128))

    # Relacionamento de pais e filhos na tabela 'medidores'
    filhos = db.relationship('Medidor',
                             secondary=genealogia,
                             primaryjoin=(genealogia.c.paiID == id),
                             secondaryjoin=(genealogia.c.filhoID == id),
                             backref=db.backref('pais', lazy='dynamic'),
                             lazy='dynamic')
    # pais.all() = [todos os medidores acima do medidor atual]
    
    # Relação com a tabela 'usuarios'
    usuarioID = db.Column(db.Integer, db.ForeignKey('usuarios.id'))
    # Relação com a tabela 'modelos_dos_medidores'
    modelo_do_medidorID = db.Column(db.Integer, db.ForeignKey('modelos_dos_medidores.id'))
    # Relação com a tabela 'metas'    
    metaID = db.Column(db.Integer, db.ForeignKey('metas.id'))

    # Relação com as tabela 'medicoes'
    medicoes = db.relationship('Medicao', backref='medidor', lazy='dynamic')


    ### MÉTODOS ###
    '''
    def addPai(self, medidor):
        if not self.ehFilho(medidor):
            self.pais.append(medidor)    # não precisa de medidor.filhos.append(self) ???
            db.sesson.add(self)
    '''

    # Representação no shell
    def __repr__(self):
        return '<Medidor ID: %s>' % self.nome

    # Representação na interface
    def __str__(self):
        return self.nome


class Meta(db.Model):
    __tablename__ = 'metas'
    id = db.Column(db.Integer, primary_key=True)
    # Descrição da meta
    descricao = db.Column(db.String(64), index=True)
    # Valor numérico da meta
    valor = db.Column(db.Float)
    # Unidade de medida da meta
    unidadeDoValor = db.Column(db.String(20))
    # Início da meta, por exemplo: 10/03/2017
    inicio = db.Column(db.DateTime, index=True)
    # Intervalo de tempo para atingir a meta (Dia, Semana, Mês, Ano)
    intervalo = db.Column(db.Interval)
    # Tempo entre realtórios por e-mail ao usuário sobre a meta
    notificacoes = db.Column(db.Interval)

    # Guardando num número binário se um alerta já foi emitido, sendo os bits da seguinte ordem:
    # [notificadoDia100%, notificadoMes100%, notificadoDia80%, notificadoMes80%]
    notificado = db.Column(db.Integer, default=0b0000)
    

    # Relação medidor de uma meta
    medidores = db.relationship('Medidor', backref='meta', lazy='dynamic')    


    '''         MÉTODOS         '''
    def foiNotificado(self, intervalo, porcento):
        switch = {
             "100%": {
                 "dia": self.notificado & 0b1000 == 0b1000
                ,"mês": self.notificado & 0b0100 == 0b0100
            }
            ,"80%": {
                 "dia": self.notificado & 0b0010 == 0b0010
                ,"mês": self.notificado & 0b0001 == 0b0001
            }
        }
        return switch[porcento][intervalo]


    def notificar(self, intervalo, porcento):
        if not self.foiNotificado(intervalo, porcento): # segurança para não mandar o e-mail mais de uma vez
            # Mandando o e-mail
            assunto = {
                 "100%": {
                     "dia": "Ultrapassagem de meta diária!"
                    ,"mês": "Ultrapassagem de meta mensal!"  
                }
                ,"80%": {
                     "dia": "80% da meta diária atingida"
                    ,"mês": "80% da meta mensal atingida"
                }
            }
            
            
            sendAsyncEmail.delay({
                 "para":     self.medidores.first().usuario.email
                ,"assunto":  assunto[porcento][intervalo]
                ,"template": 'email/alertaMeta'
                ,"kwargs": {
                     "intervaloMeta": intervalo
                    ,"porcentagem":   porcento
                }
                })


            # Atualizando o database

            switch = {
                 "100%": {
                     "dia": self.notificado | 0b1000
                    ,"mês": self.notificado | 0b0100
                }
                ,"80%": {
                     "dia": self.notificado | 0b0010
                    ,"mês": self.notificado | 0b0001
                }
            }
            
            self.notificado = switch[porcento][intervalo]
            db.session.add(self)
            db.session.commit()


    def desnotificar(self, intervalo, porcento):
        switch = {
             "100%": {
                 "dia": self.notificado & 0b0111
                ,"mês": self.notificado & 0b1011
            }
            ,"80%": {
                 "dia": self.notificado & 0b1101
                ,"mês": self.notificado & 0b1110
            }
        }
        self.notificado = switch[porcento][intervalo]
        db.session.add(self)
        db.session.commit()        




    # Representação no shell
    def __repr__(self):
        return '<Meta: %s>' % self.descricao

    # Representação na interface
    def __str__(self):
        return self.descricao

    



class Medicao(db.Model):
    __tablename__ = 'medicoes'    # nome da tabela no banco de dados
    id = db.Column(db.Integer, primary_key = True)
    # Número de pulsos, enviado pelo medidor
    valor = db.Column(db.Float)
    # Data e hora que o servidor recebeu a medição
    dataHora = db.Column(db.DateTime, index = True, default=datetime.utcnow)

    # Relação com a tabela 'medidores'
    medidorID = db.Column(db.Integer, db.ForeignKey('medidores.id'))


    ### Métodos ###

    # Converter uma medição em JSON
    def to_json(self):
        jsonMedicao = {
            'valor':    self.valor,
            'dataHora': self.dataHora
        }
        return jsonMedicao

    # Converter um JSON numa Medicao
    @staticmethod
    def from_json(jsonMedicao):
        valorDaMedicao = jsonMedicao.get('valor')
        if valorDaMedicao is None:
            return 'bug'
            # raise ValidationError('dado enviado sem valor atribuído')
        return Medicao(valor = valorDaMedicao)

    # Representação no shell
    def __repr__(self):
        return '<Medição: %s>' % self.id

    # Representação na interface
    def __str__(self):
        return self.id




###############################################################################################################################
# Formulário de aquisição de dados
class DataForm(Form):
    dado = FloatField('Insira um float:')
    submit = SubmitField('Enviar')



###############################################################################################################################



# View functions do webapp
@app.route('/', methods = ['GET', 'POST'])
def index():
    # mesAtual = Dado.query.filter(extract('month', Dado.dataHora) == datetime.datetime.utcnow().month).all()

    minhaMeta = Meta.query.first()

    minhaMeta = {
         "valor": {
             "m³": minhaMeta.valor
            ,"L":  minhaMeta.valor*1000
            ,"R$": minhaMeta.valor*PM
        }
        ,"unidadeDoValor": minhaMeta.unidadeDoValor
        ,"inicio": minhaMeta.inicio.isoformat()+'Z'
        ,"fim": (minhaMeta.inicio + minhaMeta.intervalo).isoformat()+'Z'
    }

    eu = Usuario.query.first()
    eu = {
         "nome": eu.nome
        ,"email": eu.email
        ,"senha": eu.senhaHash
    }


    # 21 dados para poder calcular 20 últimas vazões
    dados_ultimos21 = Medicao.query.all()[-21:]
    vazao_ultimos20 = []
    dataHora_ultimos20 = []
    tempTxt = ''
    temp = 0
    for i in range(1,len(dados_ultimos21)):
        '''
        Os dados em formato datetime serão utilizados no lado do cliente em conjunto com a biblioteca moment.js
        para oferecer a conversão da data e hora de acordo com a localização e configuração do usuário. O que vai
        acontecer no javascript do cliente é:
        moment("2012-12-31T23:55:13Z").format('LLLL');
        Pra isso tem que ser enviado no lugar do objeto datetime uma string usando isoformat(), como:
        obj.isoformat();
        que coloca um 'T' entre a data e a hora e depois adicionar um 'Z' no final da string pro moment.js
        reconhecer a parada
        '''
        tempTxt = dados_ultimos21[i].dataHora.isoformat()+'Z'
        dataHora_ultimos20.append(tempTxt)
        '''
        (60 s/min)*(1/1000 L/mL)*(cte mL/pulsos)*(intervaloDeConsumo pulsos)/(intervaloDeTempo s)
            = 0.06*cte*intervaloDeConsumo/intervaloDeTempo L/min
        '''
        temp = (0.06*cte)*(dados_ultimos21[i].valor - dados_ultimos21[i-1].valor)/\
               (dados_ultimos21[i].dataHora - dados_ultimos21[i-1].dataHora).total_seconds() # L/min
        vazao_ultimos20.append(temp)
    
    '''        CONSUMO ACUMULADO NO DIA E NO MÊS         '''
    # consumo do mês em m³
    dado_ultimo = dados_ultimos21[-1]
    consumoMes = dado_ultimo.valor*cte/1000000 # m³
    # consumo do dia em m³
    # Primeiro pega-se a última medição do dia anterior. No caso utilizamos '<=' para o caso que o medidor não tenha consumo no
    # dia anterior
    agora = datetime.utcnow()
    ultimaMedicaoOntem = \
        Medicao.query.filter(Medicao.dataHora <= datetime(agora.year, agora.month, agora.day)).order_by(Medicao.id.desc()).first()
    consumoDia = ultimaMedicaoOntem.valor if (ultimaMedicaoOntem is not None) else 0 # caso não exista consumo nos dias anteriores
    consumoDia = consumoMes - consumoDia*cte/1000000 # m³

    # consumos em m³, L e R$
    consumoMes = {
         "m³": consumoMes
        ,"L":  consumoMes*1000
        ,"R$": consumoMes*PM
    }
    consumoDia = {
         "m³": consumoDia
        ,"L":  consumoDia*1000
        ,"R$": consumoDia*PM
    }

    # dicionário final
    consumoDiaMes = {
         "dia": consumoDia
        ,"mês": consumoMes
    }


    consumoAcumuladoTotal = {
         "valor": Medicao.query.order_by(Medicao.id.desc()).first().valor # pegando o último valor
        ,"dataHora": Medicao.query.first().dataHora.isoformat()+'Z'
    }
    consumoAcumuladoTotal['valor'] = cte*consumoAcumuladoTotal['valor']/1000000 # m³

    historico_1mes = {
         "valor": {
             "m³": []
            ,"L":  []
            ,"R$": []
        }
        ,"dataHora": []
    }
    # supondo mẽs com 31 dias
    temp = []
    for i in range(32,0,-1): #[32, 30, ..., 2, 1]
        # última medição do dia i-ésimo dia anterior
        temp2 = Medicao.query.filter(extract('day', Medicao.dataHora) == datetime.utcnow().day-i).order_by(Medicao.id.desc()).first()
        if temp2 is not None:
            temp.append(temp2)
        if len(temp) > 1:
            consumoDoDia = (temp[-1].valor - temp[-2].valor)*cte/1000000 # m³
            historico_1mes["valor"]["m³"].append(consumoDoDia)
            historico_1mes["valor"]["L"].append(consumoDoDia*1000)
            historico_1mes["valor"]["R$"].append(consumoDoDia*PM)
            # Formato de dataHora para a biblioteca plotly.js
            historico_1mes["dataHora"].append("%d-%d-%d" %(temp[-1].dataHora.year, temp[-1].dataHora.month, temp[-1].dataHora.day))
    
    
    return render_template('painelDeControle.html', async_mode=socketio.async_mode, cte=cte, PM=PM,
                                         vazao_ultimos20=vazao_ultimos20, dataHora_ultimos20=dataHora_ultimos20, 
                                         consumoDiaMes=consumoDiaMes, minhaMeta=minhaMeta, eu=eu, 
                                         consumoAcumuladoTotal=consumoAcumuladoTotal, historico_1mes=historico_1mes)


# Página para enviar dados, simulando o trabalho do smartmeter
@app.route('/enviar', methods = ['GET', 'POST'])
def enviar():
    form = DataForm()
    if form.validate_on_submit():
        flash('Dado enviado.')
        valor = form.dado.data
        form.dado.data = ''
        novaMedicao = Medicao(valor = valor)
        db.session.add(novaMedicao)
        db.session.commit()

        minhaMeta = Meta.query.first()
        analisarMetaNotificar(minhaMeta)


        return redirect(url_for('enviar'))
    return render_template('enviar.html', form = form)



# Construindo a API dentro da aplicação
'''
Usando o HTTPie
http -v POST :5000/api/dados/ "valor=1348"
'''
@app.route('/api/dados/', methods = ['POST'])
def postDado():
    medicao = Medicao.from_json(request.json)
    db.session.add(medicao)
    db.session.commit()

    minhaMeta = Meta.query.first()
    analisarMetaNotificar(minhaMeta)

    return jsonify(medicao.to_json()), 201


@app.errorhandler(404)
def page_not_found(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        response = jsonify({'erro': 'não encontrado'})
        response.status_code = 404
        return response
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        response = jsonify({'erro': 'problemas no servidor'})
        response.status_code = 500
        return response
    return render_template('500.html'), 500


###############################################################################################################################

# SOCKET IO #

"""Example of how to send server generated events to clients."""
def background_thread():
    with app.app_context():    # usando app_context para poder acessar o db
        ultimaMedicao = Medicao.query.all()[-1]
        cte = 4.06504065
        while True:
            socketio.sleep(1)
            if ultimaMedicao != Medicao.query.all()[-1]:
                [penultima, ultimaMedicao] = Medicao.query.all()[-2:]
                vazaoAtual = (0.06*cte)*(ultimaMedicao.valor - penultima.valor)/\
                             (ultimaMedicao.dataHora - penultima.dataHora).total_seconds() # L/min
               
                '''        CONSUMO ACUMULADO NO DIA E NO MÊS         '''
                # consumo do mês em m³
                consumoMes = ultimaMedicao.valor*cte/1000000 # m³
                # consumo do dia em m³
                # Primeiro pega-se a última medição do dia anterior. No caso utilizamos '<=' para o caso que o medidor não tenha consumo no
                # dia anterior
                agora = datetime.utcnow()
                ultimaMedicaoOntem = \
                    Medicao.query.filter(Medicao.dataHora <= datetime(agora.year, agora.month, agora.day)).order_by(Medicao.id.desc()).first()
                consumoDia = ultimaMedicaoOntem.valor if (ultimaMedicaoOntem is not None) else 0 # caso não exista consumo nos dias anteriores
                consumoDia = consumoMes - consumoDia*cte/1000000 # m³

                # consumos em m³, L e R$
                consumoMes = {
                     "m³": consumoMes
                    ,"L":  consumoMes*1000
                    ,"R$": consumoMes*PM
                }
                consumoDia = {
                     "m³": consumoDia
                    ,"L":  consumoDia*1000
                    ,"R$": consumoDia*PM
                }

                # dicionário final
                consumoDiaMes = {
                     "dia": consumoDia
                    ,"mês": consumoMes
                }

                socketio.emit('my_response'
                             ,{'vazaoAtual': vazaoAtual, 'dataHora': ultimaMedicao.dataHora.isoformat()+'Z', 'consumoDiaMes': consumoDiaMes}
                             ,namespace='/test')
            


@socketio.on('disconnect_request', namespace='/test')
def disconnect_request():
    session['receive_count'] = session.get('receive_count', 0) + 1
    # emit('my_response', {'data': 'Disconnected!', 'count': session['receive_count']})
    disconnect()


@socketio.on('my_ping', namespace='/test')
def ping_pong():
    emit('my_pong')


@socketio.on('connect', namespace='/test')
def test_connect():
    global thread
    if thread is None:
        thread = socketio.start_background_task(target=background_thread)
    emit('statusConexao', {'data': 'Connectado ao servidor'})


@socketio.on('disconnect', namespace='/test')
def test_disconnect():
    print('Client disconnected', request.sid)

##############################################################################################################################
'''
def agendamento(app):
    import schedule
    import time

    def job():
        print("I'm working...")


    schedule.every(10).seconds.do(job)
    
    schedule.every(10).minutes.do(job)
    schedule.every().hour.do(job)
    schedule.every().day.at("10:30").do(job)
    schedule.every().monday.do(job)
    schedule.every().wednesday.at("13:15").do(job)
    

    while True:
        schedule.run_pending()
        time.sleep(1)

'''
if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0') 
    #manager.run()