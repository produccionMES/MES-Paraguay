from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, Response, make_response, session
from sqlalchemy import text, func
from sqlalchemy.pool import NullPool
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import csv
import io
import sqlite3
import os
import uuid
import re





app = Flask(__name__)
app.secret_key = 'wms_secreto_123' 
app.config['SECRET_KEY'] = 'wms_secreto_123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///wms_database.db'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 280,   # Recicla las conexiones antes de que el servidor las mate (cada 280 seg)
    'pool_pre_ping': True  # Verifica que la conexión esté viva antes de enviar datos
}
app.config['JWT_SECRET_KEY'] = 'mi_clave_super_secreta_wms_2026' 
app.config['CARPETA_FOTOS_REPUESTOS'] = 'static/img/repuestos'
os.makedirs(app.config['CARPETA_FOTOS_REPUESTOS'], exist_ok=True)
app.config['CARPETA_MANTENIMIENTO'] = 'static/img/mantenimiento'
os.makedirs(app.config['CARPETA_MANTENIMIENTO'], exist_ok=True)
app.config['CARPETA_TEMP'] = 'temp_uploads'
os.makedirs(app.config['CARPETA_TEMP'], exist_ok=True)


# --- CONFIGURACIÓN DE BASE DE DATOS (INTELIGENTE) ---

if os.environ.get('DATABASE_URL'):
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_recycle': 280,
        'pool_pre_ping': True
    }
else:
    # --- MODO LOCAL (TU COMPUTADORA) ---
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'

# 🔥 ESTA ES LA LÍNEA QUE FALTABA AGREGAR 🔥
db = SQLAlchemy(app)

def hora_argentina():
    return datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))

login_manager = LoginManager(app)
login_manager.login_view = 'login'



# --- CONFIGURACIÓN DE SEGURIDAD ---
login_manager.init_app(app)
login_manager.login_view = 'login' # Si alguien sin sesión intenta entrar, lo manda a esta ruta

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))

@app.before_request
def hacer_sesion_permanente():
    # Esto le dice a Flask que refresque el reloj a 1 hora 
    # cada vez que el usuario hace click o cambia de pantalla.
    session.permanent = True

# --- NUEVA TABLA: Usuarios ---
# --- TABLA: Usuarios ---
class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(250), nullable=False)
    rol = db.Column(db.String(100), nullable=False, default='operario') # ¡NUEVA COLUMNA!
    sector = db.Column(db.String(50), default='logistica')

class Rack(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    descripcion = db.Column(db.String(100))
    niveles = db.Column(db.Integer, nullable=False)
    posiciones = db.Column(db.Integer, nullable=False)
    tipo_pos = db.Column(db.String(20), default='secuencial')
    multi_nivel = db.Column(db.Integer, default=1)
    tipo = db.Column(db.String(50), default='estante')
    
    # 🔥 NUEVO: El Muro Invisible
    sector = db.Column(db.String(50), default='logistica')
    inicio = db.Column(db.Integer, default=1) 

    orden = db.Column(db.Integer, default=0)
    deposito = db.Column(db.String(100), default='Depósito Principal')
    
    # 🔥 ¡NUEVA COLUMNA COMERCIAL PARA POSVENTA! 🔥
    # Define la lógica de la zona: TALLER, APTO, OUTLET, NO_APTO, SCRAP.
    # Logística lo ignora (queda en null).
    proposito = db.Column(db.String(50), nullable=True)
    color = db.Column(db.String(20))
    
    ubicaciones = db.relationship('Ubicacion', backref='rack', lazy=True, cascade="all, delete-orphan")

# --- NUEVA TABLA: Auditoría de Ajustes de Inventario ---
class HistorialAjuste(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=hora_argentina)
    sku = db.Column(db.String(50), nullable=False)
    descripcion = db.Column(db.String(150), nullable=False)
    cantidad_anterior = db.Column(db.Integer, nullable=False)
    cantidad_nueva = db.Column(db.Integer, nullable=False)
    motivo = db.Column(db.String(200), nullable=False)
    ubicacion = db.Column(db.String(100), nullable=False)
    usuario = db.Column(db.String(50), nullable=False)
    sector = db.Column(db.String(50), default='logistica')


class Ubicacion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rack_id = db.Column(db.Integer, db.ForeignKey('rack.id'), nullable=False)
    nivel = db.Column(db.Integer, nullable=False)
    posicion = db.Column(db.Integer, nullable=False)
    codigo_unico = db.Column(db.String(100), unique=True, nullable=False)
    estado = db.Column(db.String(20), default='Disponible')

# 👇 ESTA ES LA TABLA QUE FALTABA O ESTABA MAL UBICADA 👇
class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # 🔥 Agregamos index=True acá (Búsqueda principal)
    sku = db.Column(db.String(50), nullable=False, index=True) 
    
    # 🔥 Agregamos index=True acá (Para cuando escaneas código de barras)
    ean = db.Column(db.String(50), nullable=True, index=True)
    
    descripcion = db.Column(db.String(150), nullable=False)
    modelo = db.Column(db.String(100), nullable=True) 
    
    # 🔥 Agregamos index=True acá (Porque siempre filtramos por Logística o Posventa)
    sector = db.Column(db.String(50), default='logistica', index=True)

    empresa = db.Column(db.String(100))
    familia = db.Column(db.String(100))
    alto_cm = db.Column(db.Float, default=0.0)
    ancho_cm = db.Column(db.Float, default=0.0)
    profundidad_cm = db.Column(db.Float, default=0.0)
    unidades_x_bulto = db.Column(db.Integer, default=1)
    bultos_x_piso = db.Column(db.Integer, default=1)
    pisos_x_pallet = db.Column(db.Integer, default=1)
    bultos_x_pallet = db.Column(db.Integer, default=1)
    
    items_en_stock = db.relationship('Item', backref='producto_detalle', lazy=True, cascade="all, delete-orphan")
    imagen = db.Column(db.String(255), nullable=True, default='sin_foto.png')

# --- NUEVA TABLA: Trazabilidad de Salidas ---
class HistorialDespacho(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=hora_argentina) # Guarda la fecha y hora exacta automáticamente
    sku = db.Column(db.String(50), nullable=False)
    descripcion = db.Column(db.String(150), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    transporte = db.Column(db.String(100), nullable=False) # Ej: Andreani, OCA, Camión Propio
    origen = db.Column(db.String(100), nullable=False) # Para saber de qué hueco lo sacaron
    usuario = db.Column(db.String(50), nullable=False, default='Desconocido') # ¡NUEVA COLUMNA!

# Y así debe quedar la tabla Item conectada a Producto
class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ubicacion_id = db.Column(db.Integer, db.ForeignKey('ubicacion.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    sub_ubicacion = db.Column(db.String(50)) # Lo usamos como ID de caja o LPN
    estado_calidad = db.Column(db.String(50), default='apto') 
    observaciones = db.Column(db.String(200)) # 🔥 NUEVO: Para "Partida", "Oxidada", etc.
    fecha_ingreso = db.Column(db.DateTime, default=hora_argentina)
    # 🔥 AGREGA ESTA LÍNEA PARA ARREGLAR EL ERROR DEL AJUSTE
    ubicacion = db.relationship('Ubicacion', backref='items_en_esta_posicion', lazy=True)
    lote = db.Column(db.String(100))
    fecha_vencimiento = db.Column(db.String(50))
    estado_revision = db.Column(db.String(20), default='pendiente') # Para la bandeja
    revisor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True) # Para saber quién lo tiene

class Movimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50)) 
    fecha = db.Column(db.DateTime, default=hora_argentina)
    sku = db.Column(db.String(50))
    cantidad = db.Column(db.Integer)
    origen = db.Column(db.String(100)) # Rack-N-P
    transporte = db.Column(db.String(150)) # O "Motivo" en caso de ajuste
    usuario = db.Column(db.String(50))
    sector = db.Column(db.String(50), default='logistica')

class Transferencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    remito_nro = db.Column(db.String(20), unique=True) # Ej: R-2026-001
    sku = db.Column(db.String(50), nullable=False)
    descripcion = db.Column(db.String(200))
    cantidad = db.Column(db.Integer, nullable=False)
    estado_calidad = db.Column(db.String(20))
    fecha_envio = db.Column(db.DateTime, default=datetime.now)
    usuario_envia = db.Column(db.String(50))
    estado = db.Column(db.String(20), default='En Camino') # En Camino, Recibido

class OrdenProduccion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(100), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    lote_referencia = db.Column(db.String(100))
    estado = db.Column(db.String(50), default='Pendiente') # Pendiente, En Proceso, Finalizado
    fecha_solicitud = db.Column(db.DateTime, default=hora_argentina)
    fecha_inicio = db.Column(db.DateTime) # <-- Nuevo
    fecha_fin = db.Column(db.DateTime)    # <-- Nuevo
    descripcion = db.Column(db.String(200), default="S/D") # Para saber qué es
    operario_inicio = db.Column(db.String(100))            # Quién arrancó
    operario_fin = db.Column(db.String(100))               # Quién terminó
    origen_pedido = db.Column(db.String(50), default='Logística') # Logística o Planificación
    prioridad = db.Column(db.String(20), default='Normal')
    fecha_planificada = db.Column(db.Date)


class Reparacion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False, default=1) 
    ubicacion_origen = db.Column(db.String(100), nullable=True) 
    
    # Tiempos
    fecha_ingreso = db.Column(db.DateTime, default=datetime.now)
    fecha_inicio_reparacion = db.Column(db.DateTime, nullable=True) # 🔥 ESTO FALTABA PARA EL CRONÓMETRO
    fecha_fin = db.Column(db.DateTime, nullable=True)
    
    # Actores y Detalles
    tecnico = db.Column(db.String(50), nullable=True) 
    falla_reportada = db.Column(db.String(250), nullable=True) 
    diagnostico = db.Column(db.String(250), nullable=True)     
    repuestos = db.Column(db.String(250), nullable=True)       
    
    # Estados del flujo de trabajo
    estado = db.Column(db.String(50), default='Pendiente') 
    resolucion_calidad = db.Column(db.String(50), nullable=True) 
    
    sector = db.Column(db.String(50), default='posventa')

    fecha_inicio_reparacion = db.Column(db.DateTime, nullable=True)
    tiempo_acumulado = db.Column(db.Integer, default=0) # 🔥 Guarda segundos de sesiones previas
    fecha_primer_inicio = db.Column(db.DateTime)


class TareaPicking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.String(50))
    zona = db.Column(db.String(100))
    producto = db.Column(db.String(150))
    ubicacion_excel = db.Column(db.String(100))
    sku = db.Column(db.String(50))
    descripcion = db.Column(db.String(250))
    cantidad = db.Column(db.Integer, nullable=False, default=1)
    estado = db.Column(db.String(20), default="Pendiente") # Pendiente o Pickeado
    hora_inicio = db.Column(db.DateTime, nullable=True)
    picker = db.Column(db.String(50), nullable=True)



class Pedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_pedido = db.Column(db.String(50), unique=True, nullable=False)
    cliente = db.Column(db.String(100), nullable=True)
    estado = db.Column(db.String(20), default="Pendiente") # Pendiente, En Proceso, Completado
    fecha_creacion = db.Column(db.DateTime, default=datetime.now)
    lineas = db.relationship('LineaPedido', backref='pedido_asociado', lazy=True, cascade="all, delete-orphan")

class LineaPedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedido.id'), nullable=False)
    sku = db.Column(db.String(50), nullable=False)
    cantidad_requerida = db.Column(db.Integer, nullable=False)
    cantidad_pickeada = db.Column(db.Integer, default=0)

# TABLA 1: Lo que Planificación manda a fabricar
class OrdenFabricacion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku_terminado = db.Column(db.String(50), nullable=False) 
    cantidad = db.Column(db.Integer, nullable=False)
    fecha_limite = db.Column(db.Date, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente') 
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relación: Una orden puede tener muchos pedidos de insumos asociados
    pedidos_insumos = db.relationship('PedidoMateriaPrima', backref='orden_fab', lazy=True)

# TABLA 2: Lo que Producción pide a Materias Primas
class PedidoMateriaPrima(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    orden_id = db.Column(db.Integer, db.ForeignKey('orden_fabricacion.id'), nullable=True)
    
    # Asegurate de que tu tabla se llame 'producto'
    producto_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False) 
    
    cantidad_solicitada = db.Column(db.Integer, nullable=False)
    cantidad_entregada = db.Column(db.Integer, default=0)
    estado = db.Column(db.String(20), default='Pendiente') 
    fecha_solicitud = db.Column(db.DateTime, default=datetime.utcnow)

# --- NUEVA TABLA: Tareas para el Autoelevador / Clarckista ---
class TareaReposicion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), nullable=False)
    descripcion = db.Column(db.String(150), nullable=False)
    cantidad_solicitada = db.Column(db.Integer, nullable=False)
    
    # Origen sugerido (El sistema le dice de dónde puede bajar)
    origen_sugerido = db.Column(db.String(100), nullable=True) 
    
    # Destino (A dónde tiene que llevarlo, el hueco del nivel 1000)
    destino_requerido = db.Column(db.String(100), nullable=False)
    
    estado = db.Column(db.String(20), default="Pendiente") # Pendiente, En Proceso, Completado
    fecha_solicitud = db.Column(db.DateTime, default=hora_argentina)
    usuario_solicita = db.Column(db.String(50), nullable=False)
    clarckista = db.Column(db.String(50), nullable=True) # Quién agarró la tarea

class Configuracion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clave = db.Column(db.String(50), unique=True, nullable=False)
    valor = db.Column(db.Integer, nullable=False)

class IncidenciaComercial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_venta = db.Column(db.String(50))
    numero_reclamo = db.Column(db.String(50))
    compra_empresa = db.Column(db.String(100))
    sku = db.Column(db.String(50))
    producto = db.Column(db.String(150))
    cantidad = db.Column(db.Integer)
    fecha_compra = db.Column(db.Date)
    fecha_reclamo = db.Column(db.Date)
    quien_reporta = db.Column(db.String(100))
    nombre_cliente = db.Column(db.String(150))
    lugar_entrega = db.Column(db.String(200))
    facturacion = db.Column(db.String(100))
    motivo_devolucion = db.Column(db.Text)
    observaciones = db.Column(db.Text)
    tipo_gestion = db.Column(db.String(50))
    estado = db.Column(db.String(50), default='Abierto')
    condicion = db.Column(db.String(50))
    fecha_registro = db.Column(db.DateTime, default=datetime.now)

class RegistroVenta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nro_comprobante = db.Column(db.String(50), nullable=False) # Factura o Nro ML
    fecha_venta = db.Column(db.Date, nullable=False) # La fecha real en que se vendió
    cliente = db.Column(db.String(150))
    canal = db.Column(db.String(50)) # Ej: MercadoLibre, Web, Mostrador
    total_venta = db.Column(db.Float, default=0.0)
    
    detalles = db.relationship('DetalleVenta', backref='venta', lazy=True, cascade="all, delete-orphan")

class DetalleVenta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey('registro_venta.id'), nullable=False)
    sku = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.String(200)) # <-- AGREGAMOS ESTO
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Float, default=0.0)
    subtotal = db.Column(db.Float, default=0.0)

class Receta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # El producto que se fabrica (ej: Bicicleta)
    producto_final_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False)
    # El componente que lleva (ej: Rueda)
    insumo_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False)
    # Cantidad necesaria para fabricar UNA unidad del producto final
    cantidad_necesaria = db.Column(db.Float, nullable=False, default=1.0)
    unidad_medida = db.Column(db.String(20), default='Unidades')
    orden = db.Column(db.Integer, default=0) # 🔥 NUEVA COLUMNA

    # Relaciones para que sea fácil consultar desde el código
    producto_final = db.relationship('Producto', foreign_keys=[producto_final_id], backref='componentes_receta')
    insumo = db.relationship('Producto', foreign_keys=[insumo_id])
    formula = db.Column(db.String(100))
    condicion = db.Column(db.String(100))


class Maquina(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.String(200))
    fecha_proxima_revision = db.Column(db.Date, nullable=False)
    ultima_revision = db.Column(db.Date, nullable=True)
    ultimo_comprobante = db.Column(db.String(255), nullable=True)
    sector = db.Column(db.String(50), default='logistica')

class ConfiguracionProduccion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    almuerzo_inicio = db.Column(db.String(5), default="12:00") # Formato HH:MM
    almuerzo_fin = db.Column(db.String(5), default="13:00")
    desayuno_inicio = db.Column(db.String(5), default="09:00") 
    desayuno_fin = db.Column(db.String(5), default="09:30")   
    sku_maestro_a_medida = db.Column(db.String(50), default="CORT9999")

class PedidoCliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    cliente = db.Column(db.String(100), nullable=False)
    es_a_medida = db.Column(db.Boolean, default=False)
    sku = db.Column(db.String(50)) # Será "A MEDIDA" si no es estándar
    descripcion = db.Column(db.String(250)) # Acá va el detalle de la medida
    cantidad = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente') # Pendiente, Planificado, etc.
    vendedor = db.Column(db.String(50))


# --- CREACIÓN DE LA BASE DE DATOS ---
with app.app_context():
    db.create_all() # Esto crea las tablas vacías
    
    # 🔥 FORZAMOS LA ACTUALIZACIÓN ANTES DE CONSULTAR AL ADMIN
    try:
        db.session.execute(text("ALTER TABLE usuario ADD COLUMN sector VARCHAR(50) DEFAULT 'logistica'"))
        db.session.commit()
        print("✅ Columna 'sector' agregada a los usuarios con éxito.")
    except Exception as e:
        db.session.rollback() # Si la columna ya existe, ignora el error en silencio
        pass

    # 🔥 NUEVO: Creación automática del primer Admin
    try:
        admin_existente = Usuario.query.filter_by(username='admin').first()
        
        if not admin_existente:
            # Creamos el usuario admin con clave '1234'
            from werkzeug.security import generate_password_hash
            clave_hasheada = generate_password_hash('1234')
            
            primer_admin = Usuario(
                username='admin',
                password=clave_hasheada,
                rol='admin',
                sector='logistica' # Le damos logística por defecto
            )
            db.session.add(primer_admin)
            db.session.commit()
            print("✅ Usuario 'admin' creado automáticamente con clave '1234'")
    except Exception as e:
        print(f"⚠️ Error al verificar el admin: {e}")

@app.route('/')
@app.route('/home') # (o la ruta que uses para tu menú principal)
@login_required
def home():
    # 1. Contamos cuántos pedidos de Ventas están esperando ser planificados
    # (Asumiendo que el estado inicial es 'Pendiente')
    alertas_planificacion = PedidoCliente.query.filter_by(estado='Pendiente').count()

    # 🔥 Cuenta cualquier orden que NO esté terminada ni anulada
    alertas_produccion = OrdenProduccion.query.filter(
        OrdenProduccion.estado.notin_(['Finalizado', 'Entregado', 'Anulado'])
    ).count()

    return render_template('inicio.html', 
                           alertas_planificacion=alertas_planificacion,
                           alertas_produccion=alertas_produccion)

# --- RUTAS BÁSICAS ---
from sqlalchemy.orm import joinedload # Asegurate de que esto esté arriba en los import

@app.route('/logistica')
@login_required
def logistica():
    
    roles_permitidos = ['admin', 'jefe_logistica', 'operario_logistica', 'operario', 'ruteador', 'Ruteador', 'stock', 'supervisor', 'consultas']
    if current_user.rol not in roles_permitidos:
        flash("Acceso denegado: Este módulo es solo para personal de Logística.", "error")
        return redirect(url_for('home'))
    
    racks = Rack.query.filter_by(sector='logistica').order_by(Rack.orden.asc(), Rack.id.asc()).all()
    
    # --- CÁLCULOS DE ESTADÍSTICAS (Las consultas rápidas) ---
    q_total = db.session.query(Rack.deposito, db.func.count(Ubicacion.id)).select_from(Rack).join(Ubicacion, Rack.id == Ubicacion.rack_id).filter(Rack.sector == 'logistica').group_by(Rack.deposito).all()
    dict_total = {d: c for d, c in q_total if d}

    q_ocupados = db.session.query(Rack.deposito, db.func.count(db.func.distinct(Item.ubicacion_id))).select_from(Rack).join(Ubicacion, Rack.id == Ubicacion.rack_id).join(Item, Ubicacion.id == Item.ubicacion_id).filter(Rack.sector == 'logistica', Item.cantidad > 0).group_by(Rack.deposito).all()
    dict_ocupados = {d: c for d, c in q_ocupados if d}

    q_grises = db.session.query(Rack.deposito, db.func.count(Ubicacion.id)).select_from(Rack).join(Ubicacion, Rack.id == Ubicacion.rack_id).filter(Rack.sector == 'logistica', Ubicacion.estado == 'Bloqueada').group_by(Rack.deposito).all()
    dict_grises = {d: c for d, c in q_grises if d}

    q_hijos = db.session.query(Rack.deposito, db.func.count(Ubicacion.id)).select_from(Rack).join(Ubicacion, Rack.id == Ubicacion.rack_id).filter(Rack.sector == 'logistica', Ubicacion.estado.like('Hijo_%')).group_by(Rack.deposito).all()
    dict_hijos = {d: c for d, c in q_hijos if d}

    stats_depositos = []
    for depo in list(dict_total.keys()):
        total_huecos = dict_total.get(depo, 0)
        huecos_ocupados = dict_ocupados.get(depo, 0)
        huecos_bloqueados = dict_grises.get(depo, 0) + dict_hijos.get(depo, 0)
        huecos_usables = total_huecos - huecos_bloqueados
        huecos_vacios = huecos_usables - huecos_ocupados
        porcentaje = round((huecos_ocupados / huecos_usables) * 100, 1) if huecos_usables > 0 else 0
        stats_depositos.append({
            'nombre': depo, 'total': huecos_usables, 'ocupados': huecos_ocupados, 
            'vacios': huecos_vacios, 'porcentaje': porcentaje, 
            'color_alerta': "#dc3545" if porcentaje >= 90 else "#0d6efd"
        })

    # =====================================================================
    # 🔥 EL MOTOR V8 PARA EL HTML: PRE-CARGAMOS EL MAPA VISUAL
    # Esto elimina las 2000 consultas que estaba haciendo el HTML.
    # =====================================================================
    todas_ubis = Ubicacion.query.join(Rack).filter(Rack.sector == 'logistica')\
        .options(joinedload(Ubicacion.items_en_esta_posicion).joinedload(Item.producto_detalle)).all()

    mapa_ubis = {}
    for u in todas_ubis:
        if u.rack_id not in mapa_ubis:
            mapa_ubis[u.rack_id] = {}
        
        ocupada = False
        tiene_sub = False
        if u.items_en_esta_posicion:
            for item in u.items_en_esta_posicion:
                if item.sub_ubicacion and item.sub_ubicacion != 'General':
                    tiene_sub = True
                if item.cantidad > 0 and item.producto_detalle and item.producto_detalle.sku != 'SUBDIVISION_VACIA':
                    ocupada = True

        # Guardamos la data pre-masticada en el diccionario
        mapa_ubis[u.rack_id][f"{u.nivel}-{u.posicion}"] = {
            'id': u.id,
            'estado': u.estado,
            'ocupada': ocupada,
            'tiene_sub': tiene_sub
        }

    dias_config = obtener_dias_vencimiento()

    dias_alerta_maq = obtener_dias_mantenimiento()
    hoy_date = datetime.now().date()
    limite_mantenimiento = hoy_date + timedelta(days=dias_alerta_maq)
    
    maquinas_alerta = Maquina.query.filter(
        Maquina.fecha_proxima_revision <= limite_mantenimiento
    ).all()

    return render_template('deposito.html', 
                           racks=racks, 
                           stats_depositos=stats_depositos,
                           dias_alerta=dias_config,
                           mapa_ubis=mapa_ubis, # <--- Le pasamos el mapa limpio al HTML
                           maquinas_alerta=maquinas_alerta)



@app.route('/crear_rack', methods=['POST'])
@login_required
def crear_rack():
    sector_origen = request.form.get('sector', 'logistica')

    # Roles permitidos: sumamos al jefe de materias primas
    roles_admin = ['admin', 'jefe_logistica', 'jefe_materias_primas']
    if current_user.rol not in roles_admin:
        flash('⚠️ Acceso denegado.', 'error')
        return redirect(request.referrer)

    nombre = request.form.get('nombre').strip().upper()
    prefijo_tecnico = request.form.get('codigo_tecnico', nombre).strip().upper()
    descripcion = request.form.get('descripcion', '').strip()
    
    # 🔥 ACÁ ATRAPAMOS EL NOMBRE DEL DEPÓSITO DESDE EL HTML (Paso 3)
    nombre_deposito = request.form.get('deposito_rack', 'Depósito Principal').strip().upper()

    niveles = int(request.form.get('niveles'))
    posiciones = int(request.form.get('posiciones'))
    tipo_pos = request.form.get('tipo_pos', 'secuencial')
    multi_nivel = int(request.form.get('multi_nivel', 1))
    formato = request.form.get('formato', 'normal')

    # 🔥 ACÁ LO GUARDAMOS EN LA BASE DE DATOS (Añadimos deposito=nombre_deposito)
    nuevo_rack = Rack(
        nombre=nombre, descripcion=descripcion, niveles=niveles, 
        posiciones=posiciones, tipo_pos=tipo_pos, multi_nivel=multi_nivel, 
        sector=sector_origen, deposito=nombre_deposito
    )
    db.session.add(nuevo_rack)
    db.session.commit()

    # 🔥 IGUAL A POSVENTA: Si es materias_primas, el rango arranca en 0
    if sector_origen in ['posventa', 'materias_primas']:
        rango_niveles = range(0, niveles) 
    else:
        rango_niveles = range(1, niveles + 1) 

    for n in rango_niveles:
        nivel_real = n * multi_nivel
        for p in range(1, posiciones + 1):
            
            if tipo_pos == 'impares': pos_real = (p * 2) - 1
            elif tipo_pos == 'pares': pos_real = p * 2
            else: pos_real = p

            # Formateo de dígitos
            if formato == '2digitos':
                str_pos = str(pos_real).zfill(2)
                str_nivel = str(nivel_real).zfill(2)
            elif formato == '3digitos':
                str_pos = str(pos_real).zfill(3)
                str_nivel = str(nivel_real).zfill(3)
            else:
                str_pos = str(pos_real)
                str_nivel = str(nivel_real)

            # 🔥 CONSTRUCCIÓN DEL CÓDIGO
            if sector_origen == 'posventa':
                codigo = f"PV-{prefijo_tecnico}-{str_nivel}-{str_pos}-ID{nuevo_rack.id}"
            elif sector_origen == 'materias_primas':
                codigo = f"MP-{prefijo_tecnico}-{str_nivel}-{str_pos}-ID{nuevo_rack.id}"
            else:
                # LOGÍSTICA
                codigo = f"{prefijo_tecnico}-{str_pos}-{str_nivel}-ID{nuevo_rack.id}"

            nueva_ubi = Ubicacion(
                rack_id=nuevo_rack.id, 
                nivel=nivel_real, 
                posicion=pos_real, 
                codigo_unico=codigo,
                estado='Disponible'
            )
            db.session.add(nueva_ubi)

    db.session.commit()
    flash(f'✅ Rack "{nombre}" creado con éxito en {nombre_deposito}.', 'success')
    return redirect(request.referrer)

@app.route('/usuarios')
@login_required
def gestionar_usuarios():
    rol_actual = current_user.rol.lower() if current_user.rol else 'sin_rol'
    
    # 🔥 FIX 1: Dejamos pasar al admin, jefe_logistica, jefe_produccion y ahora al JEFE_VENTAS
    if rol_actual not in ['admin', 'jefe_logistica', 'jefe_produccion', 'jefe_ventas', 'jefe_materias_primas']:
        flash('🚫 Acceso denegado. Solo administradores y jefes.', 'error')
        return redirect(url_for('logistica'))

    # Atrapamos el sector que viene en el click del botón (por defecto logistica)
    sector_filtro = request.args.get('sector', 'logistica')

    # 🔥 FIX 2: Si es Jefe de Logística, lo anclamos a su sector
    if rol_actual == 'jefe_logistica' and sector_filtro != 'logistica':
        flash('⚠️ Solo puedes gestionar los usuarios de tu sector.', 'error')
        sector_filtro = 'logistica'
        
    # 🔥 FIX 3: Si es de Producción, lo anclamos a Producción
    if rol_actual in ['jefe_produccion'] and sector_filtro != 'produccion':
        flash('⚠️ Solo puedes gestionar los usuarios de tu sector.', 'error')
        sector_filtro = 'produccion'

    # 🔥 FIX 4 (NUEVO): Si es Jefe de Ventas, lo anclamos a Ventas
    if rol_actual == 'jefe_ventas' and sector_filtro != 'ventas':
        flash('⚠️ Solo puedes gestionar los usuarios de tu sector.', 'error')
        sector_filtro = 'ventas'

    # Filtramos la lista según el sector que mandó el botón
    if sector_filtro == 'posventa':
        lista_usuarios = Usuario.query.filter_by(sector='posventa').all()
        titulo_pagina = "⚙️ Usuarios - Posventa"
    elif sector_filtro == 'materias_primas':
        lista_usuarios = Usuario.query.filter_by(sector='materias_primas').all()
        titulo_pagina = "⚙️ Usuarios - Materias Primas"
    elif sector_filtro == 'produccion':
        lista_usuarios = Usuario.query.filter_by(sector='produccion').all()
        titulo_pagina = "⚙️ Usuarios - Producción"
    elif sector_filtro == 'ventas':
        lista_usuarios = Usuario.query.filter_by(sector='ventas').all()
        titulo_pagina = "⚙️ Usuarios - Ventas"
    else:
        lista_usuarios = Usuario.query.filter_by(sector='logistica').all()
        titulo_pagina = "⚙️ Usuarios - Logística"

    # 🔥 LE PASAMOS LA VARIABLE "sector_actual" DIRECTAMENTE AL HTML
    return render_template('usuarios.html', usuarios=lista_usuarios, titulo=titulo_pagina, sector_actual=sector_filtro)

@app.route('/usuarios/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_usuario(id):
    roles_permitidos = ['admin', 'jefe_logistica', 'jefe_produccion', 'jefe_ventas', 'jefe_materias_primas', 'jefe_posventa']
    if current_user.rol.lower() not in roles_permitidos:
        flash('🚫 Solo administradores y jefes pueden modificar usuarios.', 'error')
        return redirect(request.referrer or url_for('home'))

    user = Usuario.query.get_or_404(id)
    
    # 🔥 El escudo protector del Admin
    if user.rol.lower() == 'admin' and current_user.rol.lower() != 'admin':
        flash('❌ Acción denegada: Un Jefe no puede eliminar a un Administrador.', 'error')
        return redirect(request.referrer)

    if user.id == current_user.id:
        flash('❌ No puedes eliminarte a ti mismo.', 'error')
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f'✅ Usuario {user.username} eliminado.', 'success')
        
    # 🔥 FIX: Volvemos exactamente a la pestaña donde estábamos
    return redirect(request.referrer)

@app.route('/usuarios/reset_password/<int:id>', methods=['POST'])
@login_required
def reset_password(id):
    roles_permitidos = ['admin', 'jefe_logistica', 'jefe_produccion', 'jefe_ventas', 'jefe_materias_primas', 'jefe_posventa']
    if current_user.rol.lower() not in roles_permitidos:
        return redirect(request.referrer or url_for('home'))

    user = Usuario.query.get_or_404(id)
    
    # 🔥 El escudo protector del Admin
    if user.rol.lower() == 'admin' and current_user.rol.lower() != 'admin':
        flash('❌ Acción denegada: Un Jefe no puede cambiar la contraseña del Administrador.', 'error')
        return redirect(request.referrer)

    nueva_pass = request.form.get('nueva_password')
    if nueva_pass:
        user.password = generate_password_hash(nueva_pass)
        db.session.commit()
        flash(f'✅ Contraseña de {user.username} actualizada.', 'success')
        
    # 🔥 FIX: Volvemos exactamente a la pestaña donde estábamos
    return redirect(request.referrer)

@app.route('/eliminar_rack/<int:rack_id>', methods=['POST'])
@login_required
def eliminar_rack(rack_id):
    # 1. Seguridad: Solo Admin
    if current_user.rol not in ['admin', 'jefe_logistica', 'jefe_posventa']:
        flash("⚠️ Solo un administrador o Jefe puede eliminar racks.", "error")
        return redirect(request.referrer)

    rack = Rack.query.get_or_404(rack_id)

    # 🛡️ VALIDACIÓN DE STOCK INTELIGENTE
    # Buscamos si existe algún 'Item' real (ignorando las cajas vacías)
    tiene_stock_real = db.session.query(Item).join(Ubicacion).join(Producto).filter(
        Ubicacion.rack_id == rack_id,
        Producto.sku != 'SUBDIVISION_VACIA' # <-- LA MAGIA: Ignora las cajas fantasma
    ).first()

    if tiene_stock_real:
        # Si encuentra mercadería de verdad, bloqueamos
        flash(f"❌ No se puede eliminar el rack '{rack.nombre}' porque contiene productos reales. Debes despacharlos o ajustarlos a 0 primero.", "error")
    else:
        # Si está completamente vacío (o solo tiene cajas de subdivisión vacías), procedemos
        try:
            # 🧹 Limpieza profunda: Borramos primero las subdivisiones vacías de ese rack
            items_vacios = db.session.query(Item).join(Ubicacion).filter(Ubicacion.rack_id == rack_id).all()
            for caja in items_vacios:
                db.session.delete(caja)
            
            # Ahora sí, destruimos el estante físico
            nombre_borrado = rack.nombre
            db.session.delete(rack)
            
            # Guardamos todos los cambios juntos
            db.session.commit()
            flash(f"✅ Rack '{nombre_borrado}' (y sus subdivisiones vacías) eliminado correctamente.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error técnico al eliminar: {str(e)}", "error")

    return redirect(request.referrer)

@app.route('/detalle_ubicacion/<int:rack_id>/<int:pos>/<int:nivel>')
@login_required
def detalle_ubicacion(rack_id, pos, nivel):
    # 🛡️ FIX DE ACCESO: Lista de roles permitidos (incluye administrativo)
    roles_con_acceso = [
        'admin', 'jefe_logistica', 'jefe_posventa', 'jefe_materias_primas', 
        'stock', 'posventa','administrativo', 'supervisor', 'operario_logistica', 'operario', 'encargado'
    ]
    
    if current_user.rol.lower() not in roles_con_acceso:
        flash("🚫 Acceso denegado: No tienes permisos para ver el detalle de ubicaciones.", "error")
        return redirect(url_for('home'))
    
    # 1. 🔥 BUSCAMOS EL RACK AL PRINCIPIO (Evita el error UnboundLocalError)
    rack = Rack.query.get_or_404(rack_id)

    # 🛑 EL MURO DEL TALLER PARA ADMINISTRATIVOS
    if current_user.rol.lower() == 'administrativo' and rack.proposito == 'TALLER':
        flash("🚫 Acceso denegado: El personal administrativo no puede ingresar a la zona TALLER.", "error")
        return redirect(request.referrer or url_for('posventa'))

    # 2. Buscamos la ubicación física
    ubicacion = Ubicacion.query.filter_by(rack_id=rack_id, posicion=pos, nivel=nivel).first()
    
    # 3. Si la ubicación no existe (porque la borramos con el botón rojo), la re-creamos en el acto
    if not ubicacion:
        # Usamos la variable 'rack' que ya buscamos arriba
        numero_rack = rack.nombre.split(' ')[-1] if ' ' in rack.nombre else rack.id
        
        # Logística va "limpio", solo PV y MP llevan prefijo
        if rack.sector == 'posventa':
            prefijo = "PV-"
        elif rack.sector == 'materias_primas':
            prefijo = "MP-"
        else:
            prefijo = "" 
            
        # 🔥 FORMULA CORREGIDA: Prefijo - Rack - Posición - Nivel
        codigo_nuevo = f"{prefijo}{numero_rack}-{pos}-{nivel}-ID0"
        
        ubicacion = Ubicacion(rack_id=rack.id, nivel=nivel, posicion=pos, codigo_unico=codigo_nuevo)
        db.session.add(ubicacion)
        db.session.commit()
        
        # Le inyectamos el "Fantasma" de Posición Libre
        prod_fantasma = Producto.query.filter_by(sku='SUBDIVISION_VACIA').first()
        if prod_fantasma:
            item_vacio = Item(ubicacion_id=ubicacion.id, producto_id=prod_fantasma.id, cantidad=0, estado_calidad='apto', sub_ubicacion='General')
            db.session.add(item_vacio)
            db.session.commit()

    # 4. Traemos TODOS los registros de esa posición (reales y vacíos)
    items_raw = Item.query.filter_by(ubicacion_id=ubicacion.id).all()
    
    # Ordenamos (General primero)
    items_ordenados = sorted(items_raw, key=lambda x: (x.sub_ubicacion != 'General', x.sub_ubicacion))

    # 5. Preparar la lista para el MENÚ DESPLEGABLE (Cajas donde guardar)
    sub_list = list(set([i.sub_ubicacion for i in items_raw if i.sub_ubicacion and i.sub_ubicacion != 'General']))
    sub_list.sort() 
    if len(sub_list) == 0:
        sub_list.insert(0, 'General')

    # 6. MANTENEMOS EL FILTRO SOLO PARA EL BUSCADOR DE CARGA
    if ubicacion.rack.sector == 'posventa':
        productos = Producto.query.filter(
            Producto.sector.in_(['posventa', 'repuestos']),
            Producto.sku != 'SUBDIVISION_VACIA'
        ).all()
    else:
        productos = Producto.query.filter(
            Producto.sector == ubicacion.rack.sector,
            Producto.sku != 'SUBDIVISION_VACIA'
        ).all()
    
    lista_racks = Rack.query.filter_by(sector=ubicacion.rack.sector).all()

    # 7. LA MAGIA COMERCIAL: Buscamos todas las zonas que tengan un propósito definido
    zonas_dinamicas = Rack.query.filter(
        Rack.sector == ubicacion.rack.sector, 
        Rack.proposito != None, 
        Rack.proposito != ''
    ).all()

    return render_template('ubicacion.html', 
                           ubicacion=ubicacion, 
                           items=items_ordenados, 
                           racks=lista_racks, 
                           subdivisiones=sub_list,
                           productos=productos,
                           zonas_operativas=zonas_dinamicas)

@app.route('/buscar')
@login_required
def buscar():
    if current_user.rol == 'operario':
        flash("🚫 Acceso denegado: Tu perfil no tiene permisos para búsquedas manuales.", "error")
        return redirect(url_for('home'))

    termino = request.args.get('q', '').strip().upper()
    sector_actual = request.args.get('sector', 'logistica') 
    f_sku = request.args.get('f_sku', '').strip().upper()
    f_desc = request.args.get('f_desc', '').strip().upper()
    f_estado = request.args.get('f_estado', '').strip().lower()

    page = request.args.get('page', 1, type=int)
    CANTIDAD_POR_PAGINA = 20

    pagination = None
    resultados = []
    total_stock = 0 

    if termino:
        query = Item.query.join(Producto).join(Ubicacion).join(Rack).filter(Rack.sector == sector_actual)
        
        query = query.filter(db.or_(
            Producto.sku.ilike(f"{termino}%"),
            Producto.descripcion.ilike(f"%{termino}%"),
            Item.lote.ilike(f"{termino}%")
        ))
        
        if f_sku:
            query = query.filter(Producto.sku.ilike(f"%{f_sku}%"))
        if f_desc:
            query = query.filter(Producto.descripcion.ilike(f"%{f_desc}%"))
            
        if f_estado:
            query = query.filter(Item.estado_calidad == f_estado)

        total_stock = query.with_entities(db.func.sum(Item.cantidad)).scalar() or 0
        
        # 🔥 LA SOLUCIÓN: Agregamos múltiples reglas de ordenamiento (Desempate estricto)
        pagination = query.order_by(
            Producto.sku.asc(),
            Rack.nombre.asc(),
            Ubicacion.nivel.asc(),
            Ubicacion.posicion.asc(),
            Item.id.asc() # El ID es único e irrepetible, desempatador final infalible
        ).paginate(page=page, per_page=CANTIDAD_POR_PAGINA, error_out=False)
        
        resultados = pagination.items

    return render_template('resultados.html', 
                           termino=termino, 
                           resultados=resultados, 
                           pagination=pagination, 
                           sector=sector_actual,
                           f_sku=f_sku, 
                           f_desc=f_desc,
                           f_estado=f_estado,
                           total_stock=total_stock)

@app.route('/buscar_nomina')
@login_required
def buscar_nomina():
    # 1. Limpiamos el texto y detectamos el sector
    termino = request.args.get('q', '').strip().upper()
    sector_actual = request.args.get('sector', 'posventa') 
    
    if not termino:
        return redirect(url_for('nomina_posventa'))

    # 🚀 MOTOR DE ALTA VELOCIDAD: Solo consulta la tabla Producto
    # Usamos termino% (sin el % al principio) para que use los ÍNDICES que creamos.
    busqueda_fefo = f"{termino}%"
    
    resultados = Producto.query.filter(
        Producto.sector == sector_actual,
        Producto.sku != 'SUBDIVISION_VACIA', # Limpieza de fantasmas
        db.or_(
            Producto.sku.like(busqueda_fefo),           # 🏎️ Velocidad luz por índice
            Producto.descripcion.ilike(f"%{termino}%") # Búsqueda flexible en nombre
        )
    ).limit(50).all() # 🛡️ Poka-yoke: No saturamos la pantalla del celular

    # Reutilizamos tu template de nómina pero mandando solo lo que encontramos
    return render_template('nomina_posventa.html', productos=resultados, sector=sector_actual, buscando=True)



@app.route('/agregar_item/<int:ubicacion_id>', methods=['POST'])
@login_required
def agregar_item(ubicacion_id):
    # 🔥 CORRECCIÓN: Se agregaron las comas faltantes en la lista de roles
    # Se estandarizó 'jefe_posventa' para que coincida con el resto de tu app.py
    roles_permitidos = ['admin', 'stock', 'posventa', 'administrativo', 'jefe_posventa', 'jefe_logistica', 'jefe_materias_primas', 'encargado']
    
    if current_user.rol.lower() not in [r.lower() for r in roles_permitidos]:
        flash('⚠️ No tienes permiso para agregar mercadería.', 'error')
        return redirect(request.referrer)

    # 1. Atrapamos los datos "crudos" primero
    producto_id_raw = request.form.get('producto_id')
    cantidad_raw = request.form.get('cantidad')
    
    # 🔥 EL FIX DE SEGURIDAD: Validamos que no vengan vacíos antes de hacer matemática
    if not producto_id_raw or str(producto_id_raw).strip() == '':
        flash('❌ Error: Debes buscar y seleccionar un producto antes de agregarlo.', 'error')
        return redirect(request.referrer)
        
    try:
        # Ahora sí es seguro convertirlos a números
        producto_id = int(producto_id_raw)
        cantidad = int(cantidad_raw)
    except ValueError:
        flash('❌ Error: La cantidad o el producto no tienen un formato numérico válido.', 'error')
        return redirect(request.referrer)
    
    # A partir de acá, todo sigue IGUAL que antes...
    sub_val = request.form.get('sub_ubicacion', 'General').strip()
    if not sub_val: sub_val = 'General'
    
    est_val = request.form.get('estado_calidad', 'apto')
    obs_nueva = request.form.get('observaciones', '').strip()
    
    # 🔥 ATRAPAMOS LOTE Y VENCIMIENTO 🔥
    lote_val = request.form.get('lote', '').strip()
    venc_val = request.form.get('fecha_vencimiento', '').strip()

    producto = Producto.query.get(producto_id)
    ubicacion = Ubicacion.query.get(ubicacion_id)

    # Variables para el historial de Posventa
    cant_anterior = 0
    cant_nueva = cantidad

    # 🔍 BUSQUEDA INTELIGENTE: Ahora también separa por lote y vencimiento
    item_existente = Item.query.filter_by(
        ubicacion_id=ubicacion_id,
        producto_id=producto_id,
        sub_ubicacion=sub_val,
        estado_calidad=est_val,
        lote=lote_val,                   
        fecha_vencimiento=venc_val       
    ).first()

    # 3. 🔍 BUSQUEDA DE CAJA VACIA (Reciclaje)
    caja_vacia = Item.query.filter_by(
        ubicacion_id=ubicacion_id, 
        sub_ubicacion=sub_val
    ).join(Producto).filter(Producto.sku == 'SUBDIVISION_VACIA').first()

    if item_existente:
        # 🔥 CASO A: YA EXISTE -> SUMAMOS
        cant_anterior = item_existente.cantidad
        item_existente.cantidad += cantidad
        cant_nueva = item_existente.cantidad

        if obs_nueva:
            if item_existente.observaciones:
                item_existente.observaciones += f" | {obs_nueva}"
            else:
                item_existente.observaciones = obs_nueva
        flash(f'✅ Se sumaron {cantidad} unidades al stock existente.', 'success')

    elif caja_vacia:
        # 🔥 CASO B: LA CAJA ESTABA VACIA -> LA LLENAMOS
        caja_vacia.producto_id = producto_id
        caja_vacia.cantidad = cantidad
        caja_vacia.estado_calidad = est_val
        caja_vacia.observaciones = obs_nueva
        caja_vacia.lote = lote_val
        caja_vacia.fecha_vencimiento = venc_val
        flash(f'✅ Se ocupó la subdivisión "{sub_val}" con el nuevo ingreso.', 'success')

    else:
        # 🔥 CASO C: NADA COINCIDE -> CREAMOS FILA NUEVA
        nuevo_item = Item(
            ubicacion_id=ubicacion_id,
            producto_id=producto_id,
            cantidad=cantidad,
            sub_ubicacion=sub_val,
            estado_calidad=est_val,
            observaciones=obs_nueva,
            lote=lote_val,                 
            fecha_vencimiento=venc_val     
        )
        db.session.add(nuevo_item)
        flash('✅ Ingreso manual registrado con éxito.', 'success')

    # 📝 REGISTRO EN EL HISTORIAL GENERAL (Movimientos/Ingresos)
    origen_txt = f"{ubicacion.codigo_unico.split('-ID')[0]} [{sub_val}]"
    mov_historial = Movimiento(
        tipo='ingreso',
        sku=producto.sku,
        cantidad=cantidad,
        origen=origen_txt, 
        usuario=current_user.username,
        sector=ubicacion.rack.sector,
        transporte=f"CARGA MANUAL ({est_val.upper()})"
    )
    db.session.add(mov_historial)

    # 🔥 NUEVO: REGISTRO ESPECIAL SOLO PARA POSVENTA (Para que aparezca en su pestaña de Ajustes)
    if ubicacion.rack.sector == 'posventa':
        ajuste_pv = HistorialAjuste(
            sku=producto.sku,
            descripcion=producto.descripcion,
            cantidad_anterior=cant_anterior,
            cantidad_nueva=cant_nueva,
            motivo=f"Carga Manual (+{cantidad}u)",
            ubicacion=origen_txt,
            usuario=current_user.username,
            sector='posventa'
        )
        db.session.add(ajuste_pv)

    db.session.commit()
    ejecutar_radar_interno()

    return redirect(request.referrer)

@app.route('/catalogo')
@login_required
def catalogo():
    # Solo mostramos el catálogo, no subimos archivos aquí
    productos = Producto.query.order_by(Producto.sku).all()
    return render_template('catalogo.html', productos=productos)

@app.route('/eliminar_item/<int:item_id>', methods=['POST'])
@login_required
def eliminar_item(item_id):
    # 1. Seguridad estricta: Solo jefatura puede tirar la caja a la basura
    if current_user.rol not in ['admin', 'jefe_logistica', 'jefe_posventa']:
        flash("⚠️ Solo Jefatura puede eliminar subdivisiones permanentemente.", "error")
        return redirect(request.referrer)

    item_a_borrar = Item.query.get_or_404(item_id)
    ubicacion = Ubicacion.query.get(item_a_borrar.ubicacion_id)

    # 2. Control anti-accidentes: No borrar si tiene mercadería real adentro
    if item_a_borrar.cantidad > 0 and item_a_borrar.producto_detalle.sku != 'SUBDIVISION_VACIA':
        flash("❌ No puedes destruir una subdivisión que tiene stock real adentro. Despáchalo o ajustalo a cero primero.", "error")
        return redirect(request.referrer)

    # 3. Borramos la caja física de la base de datos
    db.session.delete(item_a_borrar)
    db.session.commit()

    flash("🗑️ Subdivisión eliminada del estante para siempre.", "success")
    return redirect(url_for('detalle_ubicacion', rack_id=ubicacion.rack_id, nivel=ubicacion.nivel, pos=ubicacion.posicion))



@app.route('/descontar_item/<int:item_id>', methods=['POST'])
@login_required
def descontar_item(item_id):
    item = Item.query.get_or_404(item_id)
    sku_despachado = item.producto_detalle.sku
    producto_id_original = item.producto_id 
    sector_actual = item.ubicacion.rack.sector 

    try:
        cantidad_a_descontar = int(request.form.get('cantidad_descontar', 0))
    except:
        flash('❌ Cantidad no válida.', 'error')
        return redirect(request.referrer)

    if cantidad_a_descontar > item.cantidad or cantidad_a_descontar <= 0:
        flash('⚠️ Cantidad insuficiente o inválida en esta ubicación.', 'error')
        return redirect(request.referrer)

    lote_nombre = request.args.get('lote_nombre') 
    cantidad_hoja_str = request.args.get('cantidad_hoja')
    es_picking = cantidad_hoja_str is not None

    if es_picking and lote_nombre:
        tarea = TareaPicking.query.filter_by(sku=sku_despachado, zona=lote_nombre, estado='Pendiente').first()
    else:
        tarea = TareaPicking.query.filter_by(sku=sku_despachado, estado='Pendiente').first()

    cantidad_requerida = 0
    if es_picking:
        try:
            cantidad_requerida = int(cantidad_hoja_str)
        except:
            cantidad_requerida = cantidad_a_descontar
            
        if cantidad_a_descontar > cantidad_requerida:
            flash(f'❌ Error: El pedido requiere {cantidad_requerida} unidades.', 'error')
            return redirect(request.referrer)

    texto_transporte = lote_nombre if (es_picking and lote_nombre) else "Despacho Manual"
    
    if es_picking and tarea and tarea.hora_inicio:
        ahora = hora_argentina()
        duracion_segundos = int((ahora.replace(tzinfo=None) - tarea.hora_inicio.replace(tzinfo=None)).total_seconds())
        minutos_sku = duracion_segundos // 60
        segundos_sku = duracion_segundos % 60
        tiempo_formateado = f"{minutos_sku}m {segundos_sku}s" if minutos_sku > 0 else f"{segundos_sku}s" 
        texto_transporte = f"Ruta: {lote_nombre} (⏱️ {tiempo_formateado})"

    origen_txt = f"{item.ubicacion.codigo_unico.split('-ID')[0]} [Caja: {item.sub_ubicacion}]" if item.sub_ubicacion not in ['General', 'vacia', None] else item.ubicacion.codigo_unico.split('-ID')[0]

    nuevo_log = Movimiento(tipo='despacho', sku=sku_despachado, cantidad=cantidad_a_descontar, origen=origen_txt, transporte=texto_transporte, usuario=current_user.username, sector=sector_actual)
    db.session.add(nuevo_log)

    item.cantidad -= cantidad_a_descontar
    if item.cantidad <= 0:
        prod_vacio = Producto.query.filter_by(sku='SUBDIVISION_VACIA', sector=sector_actual).first()
        if not prod_vacio:
            prod_vacio = Producto(sku='SUBDIVISION_VACIA', descripcion='[ SUB-DIVISIÓN VACÍA ]', sector=sector_actual)
            db.session.add(prod_vacio)
            db.session.flush()
        item.producto_id = prod_vacio.id
        item.cantidad = 0
        item.estado_calidad = 'vacia'
        item.observaciones = 'Caja libre esperando mercadería'

    # Reposición automática... (se mantiene igual)
    base_por_rack = {}
    for r in Rack.query.filter_by(sector=sector_actual).all():
        m = db.session.query(db.func.min(Ubicacion.nivel)).filter_by(rack_id=r.id).scalar()
        base_por_rack[r.id] = m if m is not None else 1
    items_del_prod = Item.query.join(Ubicacion).join(Rack).filter(Item.producto_id == producto_id_original, Item.cantidad > 0, Rack.sector == sector_actual).all()
    stock_piso = 0
    reservas_disponibles = []
    for i in items_del_prod:
        nivel_base = base_por_rack.get(i.ubicacion.rack_id, 1)
        if i.ubicacion.nivel <= nivel_base: stock_piso += i.cantidad
        else: reservas_disponibles.append(i)
    if stock_piso <= 1:
        tarea_existente = TareaReposicion.query.filter_by(sku=sku_despachado, estado='Pendiente').first()
        if not tarea_existente and reservas_disponibles:
            reservas_disponibles.sort(key=lambda x: x.ubicacion.nivel)
            reserva = reservas_disponibles[0]
            origen_s = f"{reserva.ubicacion.codigo_unico.split('-ID')[0]}"
            nueva_tarea_repo = TareaReposicion(sku=sku_despachado, descripcion=reserva.producto_detalle.descripcion, cantidad_solicitada=reserva.cantidad, origen_sugerido=origen_s, destino_requerido=f"Pasillo {item.ubicacion.rack.nombre}", usuario_solicita='SISTEMA')
            db.session.add(nueva_tarea_repo)

    if es_picking and tarea:
        zona_lote = tarea.zona
        ahora = hora_argentina()
        
        # Tiempo SKU
        tiempo_sku = ""
        if tarea.hora_inicio:
            diff = int((ahora.replace(tzinfo=None) - tarea.hora_inicio.replace(tzinfo=None)).total_seconds())
            tiempo_sku = f" (⏱️ {diff // 60}m {diff % 60}s)"

        # Actualizamos el transporte con el tiempo real
        nuevo_log.transporte = f"Ruta: {zona_lote}{tiempo_sku}"

        if (cantidad_requerida - cantidad_a_descontar) > 0:
            tarea.cantidad -= cantidad_a_descontar
            # Reset cronómetros restantes
            for tr in TareaPicking.query.filter_by(zona=zona_lote).all(): tr.hora_inicio = ahora
            db.session.commit()
            return redirect(url_for('despachos', sku=sku_despachado, cantidad_hoja=tarea.cantidad, lote_nombre=zona_lote))
        else:
            db.session.delete(tarea)
            db.session.flush()
            # Reset cronómetros restantes
            for tr in TareaPicking.query.filter_by(zona=zona_lote).all(): tr.hora_inicio = ahora
            
            if TareaPicking.query.filter_by(zona=zona_lote).count() == 0:
                mov_inicio = Movimiento.query.filter_by(sku='🚀 LOTE INICIADO', origen=f"Ruta: {zona_lote}").order_by(Movimiento.id.asc()).first()
                inicio_lote = datetime.strptime(mov_inicio.transporte, '%Y-%m-%d %H:%M:%S') if mov_inicio else ahora
                duracion = int((ahora.replace(tzinfo=None) - inicio_lote.replace(tzinfo=None)).total_seconds())
                bandera_fin = Movimiento(tipo='despacho', sku='🏁 LOTE FINALIZADO', cantidad=0, origen=f"Ruta: {zona_lote}", transporte=f"⏱️ {duracion // 60}m {duracion % 60}s", usuario="Equipo / " + current_user.username, sector='logistica')
                db.session.add(bandera_fin)
                db.session.commit()
                return redirect(url_for('picking_detalle', lote=zona_lote))

            db.session.commit()
            return redirect(url_for('picking_detalle', lote=zona_lote))


    db.session.commit()
    return redirect(request.referrer or url_for('logistica'))

@app.route('/despachos')
@login_required
def despachos():
    lote_nombre = request.args.get('lote_nombre')
    sku_buscado = request.args.get('sku', '').strip().upper()
    
    if lote_nombre and sku_buscado:
        tarea_validar = TareaPicking.query.filter_by(sku=sku_buscado, zona=lote_nombre).first()
        if tarea_validar and tarea_validar.picker and tarea_validar.picker != current_user.username:
            flash(f"🚫 Acceso denegado: {tarea_validar.picker} es el responsable de este SKU.", "error")
            return redirect(url_for('picking_detalle', lote=lote_nombre))

    sku_buscado = request.args.get('sku', '').strip().upper()
    posicion_filtro = request.args.get('posicion_filtro', '').strip().upper()
    sector_usuario = request.args.get('sector', 'logistica') 
    lote_nombre = request.args.get('lote_nombre')
    cantidad_hoja = request.args.get('cantidad_hoja')

    # 1. Armamos la base de la búsqueda
    query = Item.query.join(Producto).join(Ubicacion).join(Rack)
    query = query.filter(Rack.sector == sector_usuario, Item.estado_calidad == 'apto')

    if sku_buscado:
        query = query.filter(db.or_(Producto.sku == sku_buscado, Producto.ean == sku_buscado))
    if posicion_filtro:
        query = query.filter(db.or_(Ubicacion.codigo_unico.like(f"%{posicion_filtro}%"), Ubicacion.codigo_unico.like(f"PV-{posicion_filtro}%")))

    # 🔥 LA CERRADURA FEFO: Ordenamos por fecha de vencimiento ascendente ANTES de traer los resultados
    query = query.order_by(Item.fecha_vencimiento.asc(), Rack.nombre, Ubicacion.nivel, Ubicacion.posicion)
    
    # Recién ahora traemos los resultados (ya vienen ordenados del más viejo al más nuevo)
    resultados = query.all()

    # 🧠 Cerebro Espacial
    base_por_rack = {}
    for r in Rack.query.filter_by(sector=sector_usuario).all():
        m = db.session.query(db.func.min(Ubicacion.nivel)).filter_by(rack_id=r.id).scalar()
        base_por_rack[r.id] = m if m is not None else 1

    stock_piso = 0
    stock_reserva = 0
    ubicacion_piso_sugerida = "S/D"
    ubicaciones_reserva = []

    if sku_buscado and resultados:
        for item in resultados:
            nivel_base = base_por_rack.get(item.ubicacion.rack_id, 1)

            if item.ubicacion.nivel <= nivel_base:
                stock_piso += item.cantidad
                if ubicacion_piso_sugerida == "S/D":
                    ubicacion_piso_sugerida = item.ubicacion.codigo_unico.split('-ID')[0]
            else:
                stock_reserva += item.cantidad
                sub_txt = f" [Caja: {item.sub_ubicacion}]" if item.sub_ubicacion and item.sub_ubicacion not in ['General', 'vacia'] else ""
                ubicaciones_reserva.append(f"{item.ubicacion.codigo_unico.split('-ID')[0]}{sub_txt} ({item.cantidad}u)")
    
    if ubicacion_piso_sugerida == "S/D" and resultados:
         ubicacion_piso_sugerida = f"Pasillo {resultados[0].ubicacion.rack.nombre} (Nivel Piso)"

    texto_reserva = " / ".join(ubicaciones_reserva) if ubicaciones_reserva else "Sin stock en altura"

    if cantidad_hoja:
        resultados_tabla = [item for item in resultados if item.ubicacion.nivel <= base_por_rack.get(item.ubicacion.rack_id, 1)]
    else:
        resultados_tabla = resultados

    # 🔥 FIX: Buscamos el ID de la tarea actual para que el botón de "Soltar" funcione en el HTML
    id_tarea = 0
    if lote_nombre and sku_buscado:
        t = TareaPicking.query.filter_by(sku=sku_buscado, zona=lote_nombre).first()
        if t: id_tarea = t.id

    return render_template('despachos.html', 
                           resultados=resultados_tabla, 
                           sku=sku_buscado, 
                           posicion_filtro=posicion_filtro,
                           sector=sector_usuario, 
                           cantidad_hoja=cantidad_hoja, 
                           lote_nombre=lote_nombre,
                           stock_piso=stock_piso, 
                           stock_reserva=stock_reserva, 
                           texto_reserva=texto_reserva, 
                           ubi_piso_sugerida=ubicacion_piso_sugerida,
                           tarea_actual_id=id_tarea) # <-- Le enviamos el ID al botón


# --- ANULAR PEDIDO (CON REGISTRO EN HISTORIAL) ---
@app.route('/anular_tarea/<int:tarea_id>', methods=['POST'])
@login_required
def anular_tarea(tarea_id):
    
    # Solo ruteadores y admin pueden anular
    if current_user.rol not in ['admin', 'ruteador', 'Ruteador', 'jefe_logistica']:
        flash('⚠️ No tienes permiso para anular tareas.', 'error')
        return redirect(request.referrer)

    tarea = TareaPicking.query.get_or_404(tarea_id)
    sku_anulado = tarea.sku
    cantidad_anulada = tarea.cantidad
    lote_anulado = tarea.zona

    # 📝 REGISTRO EN EL HISTORIAL (Antes de borrar la tarea)
    nuevo_log = Movimiento(
        tipo='anulacion',  # 🔥 Nueva etiqueta para el historial
        sku=sku_anulado,
        cantidad=cantidad_anulada,
        origen=f"Lote: {lote_anulado}",
        transporte="SISTEMA",
        usuario=current_user.username,
        sector='logistica'
    )
    db.session.add(nuevo_log)

    # 🔥 LA MAGIA APAGA-RADAR CORREGIDA: Logística solo puede anular si la fábrica todavía no lo terminó
    ordenes_fabrica = OrdenProduccion.query.filter_by(
        sku=sku_anulado, 
        lote_referencia=lote_anulado
    ).filter(OrdenProduccion.estado.in_(['Pendiente', 'En Proceso'])).all() # <-- LE SACAMOS EL 'Finalizado'
    
    for orden in ordenes_fabrica:
        orden.estado = 'Anulado'
        orden.descripcion = f"{orden.descripcion} (Anulado desde Logística)"

    # Ahora sí, borramos la tarea
    db.session.delete(tarea)
    db.session.commit()

    flash(f'🗑️ Pedido {sku_anulado} anulado correctamente. Si estaba en fábrica, también se canceló.', 'success')
    return redirect(request.referrer)

@app.route('/historial_produccion', methods=['GET', 'POST'])
@login_required
def historial_produccion():
    if current_user.rol not in ['admin', 'produccion', 'jefe_produccion', 'planificacion', 'supervisor', 'supervisor_produccion', 'supervisor_produccio']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('home'))

    fecha_desde = request.values.get('fecha_desde', '').strip()
    fecha_hasta = request.values.get('fecha_hasta', '').strip()
    sku_filtro = request.values.get('sku', '').strip()
    page = request.args.get('page', 1, type=int)

    # 🔥 FIX: Agregamos 'Anulado' para que aparezca en la tabla
    query = OrdenProduccion.query.filter(
        OrdenProduccion.estado.in_(['Finalizado', 'Entregado', 'Anulado'])
    )

    if fecha_desde:
        query = query.filter(OrdenProduccion.fecha_fin >= f"{fecha_desde} 00:00:00")
    if fecha_hasta:
        query = query.filter(OrdenProduccion.fecha_fin <= f"{fecha_hasta} 23:59:59")
    if sku_filtro:
        query = query.filter(OrdenProduccion.sku.ilike(f"%{sku_filtro}%"))

    ordenes_paginadas = query.order_by(OrdenProduccion.fecha_fin.desc()).paginate(page=page, per_page=20, error_out=False)
    
    return render_template('historial_produccion.html', 
                           ordenes=ordenes_paginadas,
                           fecha_desde=fecha_desde,
                           fecha_hasta=fecha_hasta,
                           sku_filtro=sku_filtro)


@app.route('/historial')
@login_required
def historial():
    sector_origen = request.args.get('sector', 'logistica')
    
    # 🔥 CANDADO ESTRICTO: Lista Blanca general para el Historial
    roles_permitidos = ['admin', 'jefe_logistica', 'stock', 'supervisor', 'jefe_posventa', 'posventa', 'administrativo', 'consultas']
    if current_user.rol not in roles_permitidos:
        flash("🚫 Acceso denegado: No tienes permisos para ver las auditorías.", "error")
        return redirect(url_for('home'))
    
    # 🛡️ Filtro de seguridad (El que ya tenías para que un Jefe no espíe el otro sector)
    if current_user.rol == 'jefe_posventa' and sector_origen != 'posventa':
        sector_origen = 'posventa'
    elif current_user.rol == 'jefe_logistica' and sector_origen != 'logistica':
        sector_origen = 'logistica'

    # --- 0. CAPTURAR FILTROS DE BÚSQUEDA ---
    q_sku = request.args.get('q_sku', '').strip()
    q_operario = request.args.get('q_operario', '').strip()
    q_ref = request.args.get('q_ref', '').strip() # Sirve para Carga, Ruta, Origen o Destino
    q_fecha = request.args.get('q_fecha', '').strip()

    # --- 1. CONFIGURACIÓN DE PAGINACIÓN ---
    LIMITE = 50 
    
    p_aj = request.args.get('p_aj', 1, type=int)
    p_in = request.args.get('p_in', 1, type=int)
    p_des = request.args.get('p_des', 1, type=int)
    p_anu = request.args.get('p_anu', 1, type=int)
    p_mov = request.args.get('p_mov', 1, type=int)
    p_inc = request.args.get('p_inc', 1, type=int)
    p_fab = request.args.get('p_fab', 1, type=int) # 🔥 NUEVO: Paginación para Fábrica

    tab_activa = request.args.get('tab', 'ingresos_tab' if sector_origen == 'logistica' else 'ajustes_tab')

    # --- 2. MOTORES DE FILTRADO (LIMPIOS Y SIN DUPLICADOS) ---
    def filtrar_movimientos(query_base):
        q = query_base
        if q_sku:
            # 🔥 CORRECCIÓN: Muestra el SKU buscado O cualquier fila que empiece con la bandera 🏁
            q = q.filter(db.or_(Movimiento.sku.ilike(f"%{q_sku}%"), Movimiento.sku.ilike("🏁%")))
        
        if q_operario: q = q.filter(Movimiento.usuario.ilike(f"%{q_operario}%"))
        if q_ref: q = q.filter(db.or_(Movimiento.transporte.ilike(f"%{q_ref}%"), Movimiento.origen.ilike(f"%{q_ref}%")))
        if q_fecha: q = q.filter(db.func.date(Movimiento.fecha) == q_fecha)
        return q.order_by(Movimiento.fecha.desc())

    def filtrar_ajustes(query_base):
        q = query_base
        if q_sku: q = q.filter(HistorialAjuste.sku.ilike(f"%{q_sku}%"))
        if q_operario: q = q.filter(HistorialAjuste.usuario.ilike(f"%{q_operario}%"))
        if q_ref: q = q.filter(HistorialAjuste.ubicacion.ilike(f"%{q_ref}%")) 
        if q_fecha: q = q.filter(db.func.date(HistorialAjuste.fecha) == q_fecha)
        return q.order_by(HistorialAjuste.fecha.desc())

    # --- 3. APLICAR FILTROS A DATOS COMUNES ---
    q_ajustes = HistorialAjuste.query.filter_by(sector=sector_origen)
    ajustes = filtrar_ajustes(q_ajustes).paginate(page=p_aj, per_page=LIMITE, error_out=False)
    
    q_ingresos = Movimiento.query.filter_by(tipo='ingreso', sector=sector_origen)
    ingresos = filtrar_movimientos(q_ingresos).paginate(page=p_in, per_page=LIMITE, error_out=False)

    # 🔥 EL SALVAVIDAS: Paginador vacío para evitar el error "NoneType" en el HTML
    pag_vacia = Movimiento.query.filter_by(id=0).paginate(page=1, per_page=LIMITE, error_out=False)

    reparaciones = []
    despachos = pag_vacia
    anulados = pag_vacia
    incompletos = pag_vacia
    pedidos_fabrica = pag_vacia # 🔥 Lo iniciamos vacío

    # --- 4. APLICAR FILTROS A DATOS ESPECÍFICOS ---
    
    # 🚚 Movimientos Internos (Reubicaciones) ahora funciona para TODOS los sectores
    q_mov = Movimiento.query.filter_by(tipo='movimiento', sector=sector_origen)
    movimientos_internos = filtrar_movimientos(q_mov).paginate(page=p_mov, per_page=LIMITE, error_out=False)

    if sector_origen == 'logistica':
        q_des = Movimiento.query.filter_by(tipo='despacho', sector='logistica')
        despachos = filtrar_movimientos(q_des).paginate(page=p_des, per_page=LIMITE, error_out=False)
        
        # 🔥 EL FIX PARA ANULADOS
        q_anu = Movimiento.query.filter(
            Movimiento.tipo == 'anulacion', 
            Movimiento.sector == 'logistica', 
            Movimiento.transporte != 'Cierre Forzado Incompleto',
            Movimiento.sku != '⚠️ CIERRE FORZADO'
        )
        anulados = filtrar_movimientos(q_anu).paginate(page=p_anu, per_page=LIMITE, error_out=False)

        # 🔥 EL FIX PARA INCOMPLETOS
        q_inc = Movimiento.query.filter(
            Movimiento.tipo == 'anulacion', 
            Movimiento.sector == 'logistica', 
            db.or_(
                Movimiento.transporte == 'Cierre Forzado Incompleto',
                Movimiento.sku == '⚠️ CIERRE FORZADO'
            )
        )
        incompletos = filtrar_movimientos(q_inc).paginate(page=p_inc, per_page=LIMITE, error_out=False)

        # ====================================================================
        # 🔥 NUEVO: RASTREO DE PEDIDOS A FÁBRICA
        # ====================================================================
        q_fab = OrdenProduccion.query.filter_by(origen_pedido='Logística')
        
        if q_sku: 
            q_fab = q_fab.filter(OrdenProduccion.sku.ilike(f"%{q_sku}%"))
        if q_fecha: 
            q_fab = q_fab.filter(db.func.date(OrdenProduccion.fecha_solicitud) == q_fecha)
            
        pedidos_fabrica = q_fab.order_by(OrdenProduccion.fecha_solicitud.desc()).paginate(page=p_fab, per_page=LIMITE, error_out=False)

    return render_template('historial.html', 
                           despachos=despachos, 
                           anulados=anulados, 
                           ajustes=ajustes, 
                           ingresos=ingresos,
                           movimientos_internos=movimientos_internos,
                           reparaciones=reparaciones,
                           incompletos=incompletos,
                           pedidos_fabrica=pedidos_fabrica, # 🔥 LO ENVIAMOS AL HTML
                           sector=sector_origen,
                           tab_activa=tab_activa,
                           q_sku=q_sku, q_operario=q_operario, q_ref=q_ref, q_fecha=q_fecha)


@app.route('/ajustar_stock/<int:item_id>', methods=['POST'])
@login_required
def ajustar_stock(item_id):
    # 🛡️ BLOQUE 1: NORMALIZACIÓN ULTRA-ROBUSTA
    # Limpiamos espacios, pasamos a minúsculas y eliminamos posibles saltos de línea
    rol_original = current_user.rol if current_user.rol else ""

    # ¡Agregá esto!
    print(f"ATENCIÓN: El usuario {current_user.username} intentó ajustar stock. Su rol real en la BD es: '{rol_original}'")
    
    rol_limpio = rol_original.strip().lower()

    # 🛡️ BLOQUE 2: CHEQUEO POR PALABRA CLAVE (Más seguro)
    # Si el usuario es admin o el rol contiene las palabras clave, lo dejamos pasar
    es_admin = rol_limpio == 'admin'
    es_encargado = 'encargado' in rol_limpio
    es_jefe_mp = 'jefe_materias_primas' in rol_limpio
    es_logistica = any(r in rol_limpio for r in ['stock', 'jefe_logistica'])
    es_posventa = any(r in rol_limpio for r in ['posventa', 'jefe_posventa'])

    if not (es_admin or es_encargado or es_jefe_mp or es_logistica or es_posventa):
        # Este mensaje te va a decir EXACTAMENTE qué está viendo el sistema
        flash(f'⚠️ Acceso denegado. Tu rol detectado es: "{rol_original}". Contacte al administrador.', 'error')
        return redirect(request.referrer)

    # 📦 INICIO DE LÓGICA DE NEGOCIO
    item = Item.query.get_or_404(item_id)
    sector_detectado = item.ubicacion.rack.sector
    cantidad_anterior = item.cantidad
    
    try:
        nueva_cantidad = int(request.form.get('nueva_cantidad', 0))
    except (ValueError, TypeError):
        nueva_cantidad = 0
        
    motivo = request.form.get('motivo', 'Ajuste manual').strip()
    
    # --- ESCUDO ANTI-HUÉRFANOS ---
    if item.producto_detalle:
        sku_historial = item.producto_detalle.sku
        desc_historial = item.producto_detalle.descripcion
    else:
        sku_historial = "SKU_ELIMINADO"
        desc_historial = "Producto borrado del catálogo"

    ubi_historial = f"{item.ubicacion.codigo_unico.split('-ID')[0]} [Caja: {item.sub_ubicacion}]" if item.sub_ubicacion not in ['General', 'vacia', None] else item.ubicacion.codigo_unico.split('-ID')[0]

    # --- LÓGICA DE ACTUALIZACIÓN ---
    if nueva_cantidad <= 0:
        es_recepcion = "RECEPCI" in item.ubicacion.rack.nombre.upper()
        es_remito = str(item.sub_ubicacion).startswith('R-')
        
        if item.sub_ubicacion != 'General' and not es_recepcion and not es_remito:
            # Vaciamos pero dejamos la caja viva
            prod_vacio = Producto.query.filter_by(sku='SUBDIVISION_VACIA', sector=sector_detectado).first()
            if not prod_vacio:
                prod_vacio = Producto(sku='SUBDIVISION_VACIA', descripcion='[ SUB-DIVISIÓN VACÍA ]', sector=sector_detectado)
                db.session.add(prod_vacio)
                db.session.flush()
                
            item.producto_id = prod_vacio.id
            item.cantidad = 0
            item.estado_calidad = 'vacia'
            item.observaciones = 'Caja libre (ajustada a 0)'
            mensaje = f"✅ Stock de {sku_historial} agotado. Subdivisión vaciada."
        else:
            db.session.delete(item)
            mensaje = f"✅ Stock de {sku_historial} agotado. Registro eliminado."
    else:
        item.cantidad = nueva_cantidad
        mensaje = f"✅ Stock de {sku_historial} ajustado a {nueva_cantidad}."

    # --- REGISTRO HISTÓRICO ---
    nuevo_historial = HistorialAjuste(
        sku=sku_historial,
        descripcion=desc_historial,
        cantidad_anterior=cantidad_anterior,
        cantidad_nueva=nueva_cantidad,
        motivo=motivo,
        ubicacion=ubi_historial,
        usuario=current_user.username,
        sector=sector_detectado
    )
    db.session.add(nuevo_historial)
    
    try:
        db.session.commit()
        ejecutar_radar_interno() # Actualiza las tareas del clarckista si es necesario
        flash(mensaje, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al guardar en base de datos: {str(e)}", 'error')
    
    return redirect(request.referrer)
    
    

@app.route('/exportar_historial')
def exportar_historial():
    if current_user.rol not in ['admin', 'jefe_logistica']:
        flash("⚠️ Solo un administrador o Jefe puede hacer esto.", "error")
        return redirect(request.referrer)

    # Traemos todos los registros del historial
    registros = HistorialDespacho.query.order_by(HistorialDespacho.fecha.desc()).all()

    # Creamos un archivo en la memoria del servidor
    si = io.StringIO()
    # Usamos punto y coma para que el Excel en español lo separe en columnas automáticamente
    writer = csv.writer(si, delimiter=';')

    # Escribimos la primera fila con los títulos
    writer.writerow(['Fecha y Hora', 'Transporte', 'SKU', 'Descripción', 'Cantidad', 'Origen'])

    # Recorremos el historial y escribimos cada fila
    for r in registros:
        writer.writerow([
            r.fecha.strftime('%d/%m/%Y %H:%M'),
            r.usuario,
            r.transporte,
            r.sku,
            r.descripcion,
            r.cantidad,
            r.origen
        ])

    # utf-8-sig es el truco mágico para que Excel lea perfectamente los acentos y las ñ
    output = si.getvalue().encode('utf-8-sig')

    # Le decimos al navegador que descargue el archivo en lugar de mostrarlo
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=historial_despachos.csv"}
    )



    # --- RUTAS DE SEGURIDAD ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = Usuario.query.filter_by(username=username).first()

        # Verificamos si el usuario existe y si la contraseña (encriptada) coincide
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('home'))
        else:
            flash('Usuario o contraseña incorrectos.')

    return render_template('login.html')

@app.route('/registro', methods=['GET', 'POST'])
@login_required
def registro():
    rol_actual = current_user.rol.lower() if current_user.rol else 'sin_rol'
    
    # 🔥 FIX 1: Dejamos entrar al Jefe de Logística
    if rol_actual not in ['admin', 'jefe_logistica', 'jefe_materias_primas']:
        flash('🚫 Acceso denegado.', 'error')
        return redirect(url_for('home'))

    # Si venimos de la pantalla de Posventa, pre-seleccionamos posventa
    sector_origen = request.args.get('sector', 'logistica')

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        rol_elegido = request.form.get('rol', 'operario')
        sector_elegido = request.form.get('sector', 'logistica')

        # 🔥 FIX 2: Evitamos que el Jefe cree un usuario con poder de Admin
        if rol_elegido.lower() == 'admin' and rol_actual != 'admin':
            flash('❌ Solo un Administrador puede crear a otro Administrador.', 'error')
            return redirect(url_for('registro', sector=sector_origen))

        usuario_existente = Usuario.query.filter_by(username=username).first()
        if usuario_existente:
            flash('❌ El usuario ya existe.', 'error')
            return redirect(url_for('registro', sector=sector_origen))

        password_encriptada = generate_password_hash(password)
        
        nuevo_usuario = Usuario(
            username=username, 
            password=password_encriptada, 
            rol=rol_elegido, 
            sector=sector_elegido
        )

        db.session.add(nuevo_usuario)
        db.session.commit()

        flash(f'✅ Usuario {username} creado con éxito en {sector_elegido.upper()}.', 'success')
        
        return redirect(url_for('gestionar_usuarios', sector=sector_elegido)) 

    return render_template('registro.html', sector_origen=sector_origen)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/actualizar_db')
def actualizar_db():
    try:
        # 1. Racks (tipo_pos y multi_nivel)
        try:
            db.session.execute(text('ALTER TABLE rack ADD COLUMN tipo_pos VARCHAR(20) DEFAULT "secuencial"'))
        except: pass
        try:
            db.session.execute(text('ALTER TABLE rack ADD COLUMN multi_nivel INTEGER DEFAULT 1'))
        except: pass 

        # 🔥 COLUMNAS NUEVAS PARA DESAYUNO DE PRODUCCIÓN
        try:
            db.session.execute(text("ALTER TABLE configuracion_produccion ADD COLUMN desayuno_inicio VARCHAR(5) DEFAULT '09:00'"))
            db.session.execute(text("ALTER TABLE configuracion_produccion ADD COLUMN desayuno_fin VARCHAR(5) DEFAULT '09:30'"))
        except: pass

        # 2. Historial (sector)
        try:
            db.session.execute(text("ALTER TABLE historial_ajuste ADD COLUMN sector VARCHAR(50) DEFAULT 'logistica'"))
        except: pass

        # 3. Productos (modelo)
        try:
            db.session.execute(text("ALTER TABLE producto ADD COLUMN modelo VARCHAR(100)"))
        except: pass

        # 4. Items (observaciones y estado para Posventa) 
        try:
            db.session.execute(text("ALTER TABLE item ADD COLUMN observaciones VARCHAR(200)"))
        except: pass

        # 5. Usuarios (sector para separar Posventa de Logística)
        try:
            db.session.execute(text("ALTER TABLE usuario ADD COLUMN sector VARCHAR(50) DEFAULT 'logistica'"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE item ADD COLUMN estado_calidad VARCHAR(50) DEFAULT 'apto'"))
        except: pass

        # 6. Cronómetro de Picking
        try:
            db.session.execute(text("ALTER TABLE tarea_picking ADD COLUMN hora_inicio DATETIME"))
        except: pass
        try:
            db.session.execute(text("ALTER TABLE tarea_picking ADD COLUMN picker VARCHAR(50)"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE configuracion MODIFY COLUMN valor FLOAT"))
            db.session.commit()
        except:
            pass

        # 7. Movimientos y Racks
        try:
            db.session.execute(text("ALTER TABLE movimiento ADD COLUMN sector VARCHAR(50) DEFAULT 'logistica'"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE rack ADD COLUMN inicio INTEGER DEFAULT 1"))
        except: pass

        # 8. Reparaciones (Posventa)
        try:
            db.session.execute(text("ALTER TABLE reparacion ADD COLUMN cantidad INTEGER DEFAULT 1"))
        except: pass
        
        try:
            db.session.execute(text("ALTER TABLE reparacion ADD COLUMN fecha_inicio_reparacion DATETIME"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE reparacion ADD COLUMN ubicacion_origen VARCHAR(100)"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE reparacion ADD COLUMN tiempo_acumulado INTEGER DEFAULT 0"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE rack ADD COLUMN orden INTEGER DEFAULT 0"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE rack ADD COLUMN tipo VARCHAR(50) DEFAULT 'estante'"))
            db.session.commit()
        except: pass

        # 🔥 9. NUEVO: Estado de Ubicaciones (Bloqueo por Magnitud)
        try:
            db.session.execute(text("ALTER TABLE ubicacion ADD COLUMN estado VARCHAR(20) DEFAULT 'Disponible'"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE item ADD COLUMN lote VARCHAR(100)"))
        except: pass
        try:
            db.session.execute(text("ALTER TABLE item ADD COLUMN fecha_vencimiento VARCHAR(50)"))
        except: pass

        try:
            db.create_all() # Esto crea TareaReposicion sin romper las demás
        except: pass

        # 🔥 COLUMNAS NUEVAS PARA ZONAS DE POSVENTA
        try:
            db.session.execute(text("ALTER TABLE rack ADD COLUMN proposito VARCHAR(50)"))
        except: pass

        # 🔥 COLUMNA NUEVA PARA RECETAS (UNIDAD DE MEDIDA)
        try:
            db.session.execute(text("ALTER TABLE receta ADD COLUMN unidad_medida VARCHAR(20) DEFAULT 'Unidades'"))
            print("✅ Columna 'unidad_medida' agregada a recetas.")
        except Exception as e:
            print(f"La columna 'unidad_medida' ya existía o hubo un error: {e}")

        try:
            db.session.execute(text("ALTER TABLE rack ADD COLUMN color VARCHAR(20)"))
        except: pass

        # 🔥 NUEVA COLUMNA PARA PROGRAMACIÓN DE FECHAS DE PRODUCCIÓN
        try:
            db.session.execute(text("ALTER TABLE orden_produccion ADD COLUMN fecha_planificada DATE"))
            print("Columna 'fecha_planificada' agregada a orden_produccion.")
        except Exception as e:
            print(f"La columna 'fecha_planificada' ya existía o hubo un error: {e}")

        # 🔥 NUEVAS COLUMNAS PARA PRODUCCIÓN
        try:
            db.session.execute(text("ALTER TABLE orden_produccion ADD COLUMN descripcion VARCHAR(200)"))
            db.session.execute(text("ALTER TABLE orden_produccion ADD COLUMN operario_inicio VARCHAR(100)"))
            db.session.execute(text("ALTER TABLE orden_produccion ADD COLUMN operario_fin VARCHAR(100)"))
            print("Columnas de producción agregadas.")
        except Exception as e:
            print(f"Las columnas de producción ya existían o hubo un error: {e}")

        # 🔥 NUEVA COLUMNA PARA DETALLE DE VENTAS
        try:
            db.session.execute(text("ALTER TABLE detalle_venta ADD COLUMN descripcion VARCHAR(200)"))
            print("Columna 'descripcion' agregada a detalle_venta.")
        except Exception as e:
            print(f"La columna 'descripcion' ya existía o hubo un error: {e}")

        # 🔥 NUEVA COLUMNA DE PRIORIDAD
        try:
            db.session.execute(text("ALTER TABLE orden_produccion ADD COLUMN prioridad VARCHAR(20) DEFAULT 'Normal'"))
        except: pass

        try:
            db.session.execute(text("ALTER TABLE orden_produccion ADD COLUMN origen_pedido VARCHAR(50) DEFAULT 'Logística'"))
        except: pass

        # 🔥 11. Comprobantes de Mantenimiento
        try:
            db.session.execute(text("ALTER TABLE maquina ADD COLUMN ultimo_comprobante VARCHAR(255)"))
            db.session.commit()
            print("✅ Columna comprobante agregada a máquinas.")
        except:
            db.session.rollback()

        # 🔥 NUEVA TABLA PARA INGENIERÍA (BOM)
        try:
            db.create_all() # Crea la tabla Receta si no existe
            print("✅ Tabla de Recetas (BOM) sincronizada.")
        except Exception as e:
            print(f"Error al crear tabla de recetas: {e}")

        # 🔥 NUEVAS COLUMNAS PARA PRODUCTOS LOGÍSTICOS
        try:
            columnas_prod = [
                "empresa VARCHAR(100)", "familia VARCHAR(100)",
                "alto_cm FLOAT DEFAULT 0", "ancho_cm FLOAT DEFAULT 0", "profundidad_cm FLOAT DEFAULT 0",
                "unidades_x_bulto INTEGER DEFAULT 1", "bultos_x_piso INTEGER DEFAULT 1",
                "pisos_x_pallet INTEGER DEFAULT 1", "bultos_x_pallet INTEGER DEFAULT 1"
            ]
            for col in columnas_prod:
                try:
                    db.session.execute(text(f"ALTER TABLE producto ADD COLUMN {col}"))
                except:
                    pass # Si ya existe, la ignora en silencio
            db.session.commit()
        except:
            pass

        # ==========================================================
        # 🔥 10. NUEVA TABLA PARA TPM (MANTENIMIENTO PREVENTIVO) 🔥
        # ==========================================================
        try:
            # db.create_all() crea cualquier clase que no tenga una tabla existente en la DB
            db.create_all() 
            print("✅ Tabla de Mantenimiento de Máquinas (TPM) sincronizada.")
        except Exception as e:
            print(f"Error al crear tabla de Mantenimiento: {e}")

        db.session.commit()
        return "✅ ¡Base de datos y Clases sincronizadas! Ya podés operar normalmente."
    
    except Exception as e:
        db.session.rollback()
        return f"⚠️ Error crítico al actualizar: {str(e)}"

@app.route('/subir_pedidos', methods=['GET', 'POST'])
@login_required
def subir_pedidos():
    if current_user.rol not in ['admin', 'ruteador', 'Ruteador', 'jefe_logistica']:
        flash('⚠️ Acceso denegado.')
        return redirect(url_for('logistica'))

    if request.method == 'POST':
        archivo = request.files.get('archivo_excel')
        if not archivo:
            flash('❌ No se seleccionó ningún archivo.')
            return redirect(request.url)

        try:
            # 1. GENERAMOS UN NÚMERO ÚNICO DE CARGA (Día, Mes, Hora, Minuto)
            hora_actual = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
            nro_carga = hora_actual.strftime('%d%m%y-%H%M') 

            df = pd.read_excel(archivo)
            for _, fila in df.iterrows():
                # 🔥 ARREGLO 1: Forzamos mayúsculas (.upper()) para que coincida siempre con la DB
                sku_excel = str(fila.get('SKU', '')).strip().upper()
                
                try:
                    cantidad_pedida = int(fila.get('Cantidad', 1))
                except:
                    cantidad_pedida = 1

                if not sku_excel:
                    continue # Saltamos filas vacías

                # --- LÓGICA DE BÚSQUEDA Y AUTO-COMPLETADO ---
                
                # 🔥 ARREGLO 2: Obligamos a que busque el producto SOLO en Logística
                producto_db = Producto.query.filter(
                    ((Producto.sku == sku_excel) | (Producto.ean == sku_excel)),
                    Producto.sector == 'logistica'
                ).first()
                
                ubicacion_final = "❌ Sin Stock / No encontrado"
                
                # 🔥 LA MAGIA: AUTO-COMPLETAR DESCRIPCIÓN DESDE LA BASE DE DATOS
                descripcion_real = ""

                if producto_db:
                    # Si el producto existe en el catálogo, tomamos SU nombre oficial
                    descripcion_real = producto_db.descripcion
                    if producto_db.modelo:
                        descripcion_real += f" ({producto_db.modelo})" # Le sumamos el modelo si lo tiene

                    # Buscamos el stock
                    items_stock = Item.query.filter(Item.producto_id == producto_db.id, Item.cantidad > 0).all()
                    stock_total_disponible = sum(item.cantidad for item in items_stock)

                    if items_stock:
                        ubicaciones_list = []
                        for i in items_stock:
                            ubi_base = i.ubicacion.codigo_unico.split('-ID')[0]
                            # 🔥 LA MAGIA: Deja registro de la caja para que aparezca en el resumen
                            sub_texto = f" [Caja: {i.sub_ubicacion}]" if i.sub_ubicacion and i.sub_ubicacion not in ['General', 'vacia'] else ""
                            ubicaciones_list.append(f"{ubi_base}{sub_texto} ({i.cantidad}u)")
                        ubicacion_final = " / ".join(ubicaciones_list)

                else:
                    # Si NO está en la base de datos, usamos lo que diga el Excel (o un aviso)
                    descripcion_excel = str(fila.get('Descripción', fila.get('Descripcion', '')))
                    descripcion_real = descripcion_excel if descripcion_excel else "⚠️ SKU Desconocido en Catálogo"

                # 2. CAPTURAMOS LA COLUMNA Y LE PEGAMOS EL NÚMERO AUTOMÁTICO
                despacho_excel = str(fila.get('Despacho', fila.get('Pedido', 'General'))).strip()
                despacho_final = f"Carga #{nro_carga} - {despacho_excel}"

                nueva_tarea = TareaPicking(
                    fecha=str(fila.get('Fecha', hora_actual.strftime('%Y-%m-%d'))),
                    zona=despacho_final, 
                    producto=descripcion_real,    # 👈 ACÁ USAMOS LA DESCRIPCIÓN INTELIGENTE
                    ubicacion_excel=ubicacion_final,
                    sku=sku_excel,
                    descripcion=descripcion_real, # 👈 ACÁ TAMBIÉN
                    cantidad=cantidad_pedida,
                    estado='Pendiente'
                )
                db.session.add(nueva_tarea)

            db.session.commit()
            flash(f'✅ Hoja de ruta subida con éxito bajo la Carga #{nro_carga}.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Error al procesar Excel: {str(e)}', 'error')
        
        return redirect(url_for('subir_pedidos'))

    tareas_pendientes = TareaPicking.query.filter_by(estado='Pendiente').all()
    return render_template('subir_pedidos.html', tareas=tareas_pendientes)


@app.route('/picking')
@login_required
def ver_picking():
    if current_user.rol in ['stock', 'ruteador', 'Ruteador', 'consultas']:
        flash("Acceso denegado.", "error")
        return redirect(url_for('logistica'))

    # Traemos todas las tareas pendientes
    tareas = TareaPicking.query.filter_by(estado='Pendiente').all()

    lotes = {}
    tiempos_inicio = {} 
    stock_por_sku = {}

    for t in tareas:
        # 1. Agrupamos por lotes
        if t.zona not in lotes:
            lotes[t.zona] = []
            # 🔥 CORRECCIÓN: Solo convertimos a ISO si t.hora_inicio NO es None
            if t.hora_inicio:
                tiempos_inicio[t.zona] = t.hora_inicio.isoformat()
        
        lotes[t.zona].append(t)

        # 2. Mantener tu lógica de cálculo de stock (No la borres)
        if t.sku not in stock_por_sku:
            sku_limpio = str(t.sku).strip()
            producto = Producto.query.filter((Producto.sku.ilike(sku_limpio)) | (Producto.ean.ilike(sku_limpio))).first()
            
            if producto:
                total_stock = db.session.query(db.func.sum(Item.cantidad))\
                    .join(Ubicacion).join(Rack)\
                    .filter(Item.producto_id == producto.id, Rack.sector == 'logistica')\
                    .scalar()
                stock_por_sku[t.sku] = total_stock if total_stock else 0
            else:
                stock_por_sku[t.sku] = 0

    # 🔥 NUEVO: RASTREADOR DE PEDIDOS FINALIZADOS EN FÁBRICA (Filtrado)
    # Solo mostramos en el tablero de Logística lo que ELLOS pidieron.
    ordenes_finalizadas = OrdenProduccion.query.filter_by(
        estado='Finalizado', 
        origen_pedido='Logística'
    ).all()
    # Armamos una lista solo con los nombres de las cargas/rutas que ya tienen mercadería lista
    cargas_con_fabrica_lista = [o.lote_referencia for o in ordenes_finalizadas if o.lote_referencia]

    return render_template('picking.html', 
                           lotes=lotes, 
                           stock_por_sku=stock_por_sku, 
                           tiempos_inicio=tiempos_inicio,
                           cargas_con_fabrica_lista=cargas_con_fabrica_lista) # 👈 Se lo pasamos a la pantalla

@app.route('/picking_detalle')
@login_required
def picking_detalle():

    if current_user.rol in ['stock', 'consultas']:
        flash("Acceso denegado.", "error")
        return redirect(url_for('logistica'))

    lote = request.args.get('lote')
    if not lote:
        return redirect(url_for('ver_picking'))

    tareas = TareaPicking.query.filter_by(estado='Pendiente', zona=lote).all()

    # 🔥 FIX 1: BORRAMOS el bucle que le metía hora a todas las tareas al entrar.
    # El t.hora_inicio queda vacío (None) hasta que el operario apriete el botón.

    stock_por_sku = {} 
    ubicaciones_por_sku = {}
    ordenes_produccion_status = {}

    for t in tareas:
        sku_limpio = str(t.sku).strip()
        
        # 1. Buscamos si ya le pedimos esto a fábrica
        orden = OrdenProduccion.query.filter_by(sku=sku_limpio, lote_referencia=lote).order_by(OrdenProduccion.id.desc()).first()
        if orden:
            ordenes_produccion_status[t.sku] = orden

        # 2. Calculamos stock y ubicaciones normales CON LÓGICA FEFO
        if t.sku not in stock_por_sku:
            producto = Producto.query.filter(
                ((Producto.sku.ilike(sku_limpio)) | (Producto.ean.ilike(sku_limpio))),
                Producto.sector == 'logistica'
            ).first()
            
            if producto:
                items_reales = Item.query.join(Ubicacion).join(Rack)\
                    .filter(Item.producto_id == producto.id, Item.cantidad > 0, Rack.sector == 'logistica', Item.estado_calidad == 'apto')\
                    .order_by(Item.fecha_vencimiento.asc(), Rack.nombre, Ubicacion.nivel, Ubicacion.posicion).all()
                
                if items_reales:
                    stock_por_sku[t.sku] = sum(item.cantidad for item in items_reales)
                    lista_ubis = []
                    
                    for i, item in enumerate(items_reales):
                        rack_nombre = item.ubicacion.rack.nombre.replace("ESTANTERÍA ", "Est. ")
                        sub_texto = f" [Caja: {item.sub_ubicacion}]" if item.sub_ubicacion and item.sub_ubicacion not in ['General', 'vacia'] else ""
                        fecha_str = f" ⏳ Vence: {item.fecha_vencimiento}" if item.fecha_vencimiento else ""
                        destacado = "⭐ " if i == 0 else ""
                        
                        lista_ubis.append(f"{destacado}{rack_nombre} N{item.ubicacion.nivel}-P{item.ubicacion.posicion}{sub_texto}{fecha_str} ({item.cantidad}u)")
            
                    ubicaciones_por_sku[t.sku] = " / ".join(lista_ubis)
                else:
                    stock_por_sku[t.sku] = 0
                    ubicaciones_por_sku[t.sku] = "❌ Sin stock físico en Logística"
            else:
                stock_por_sku[t.sku] = 0
                ubicaciones_por_sku[t.sku] = "❌ Producto no encontrado en nómina"

    # 🔥 FIX 2: Buscamos la hora del "Cronómetro General" en el historial, NO en la tarea.
    bandera_general = Movimiento.query.filter_by(sku='🚀 LOTE INICIADO', origen=f"Ruta: {lote}").first()
    hora_inicio_general = None
    if bandera_general:
        # Transformamos el string guardado a un formato que el HTML entienda
        hora_inicio_general = datetime.strptime(bandera_general.transporte, '%Y-%m-%d %H:%M:%S').isoformat()

    return render_template('picking_detalle.html', 
                           tareas=tareas, 
                           lote=lote, 
                           stock_por_sku=stock_por_sku, 
                           ubicaciones_por_sku=ubicaciones_por_sku,
                           ordenes_produccion_status=ordenes_produccion_status,
                           hora_inicio_general=hora_inicio_general)

# --- BLOQUEO DE CACHÉ PARA EL BOTÓN ATRÁS ---
@app.after_request
def add_header(response):
    """Fuerza al navegador a no guardar las páginas en memoria."""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

import pandas as pd # Asegúrate de tener instalado pandas (pip install pandas openpyxl)



@app.route('/exportar_stock')
@login_required
def exportar_stock():
    # Solo el Admin o Jefe Logística pueden bajar el inventario
    if current_user.rol not in ['admin', 'jefe_logistica', 'stock', 'ruteador', 'Ruteador', 'consultas']: 
        flash("⚠️ No tienes permisos para esta acción.", "error")
        return redirect(url_for('logistica'))

    # 🔥 CORRECCIÓN 1: Le decimos a la base de datos que SOLO traiga items con cantidad mayor a 0
    items = Item.query.join(Ubicacion).join(Rack).filter(
        Rack.sector == 'logistica',
        Item.cantidad > 0
    ).all()
    
    # Creamos la lista de datos para el Excel
    data = []
    for i in items:
        # 🔥 CORRECCIÓN 2: Ignoramos las cajas vacías (SUBDIVISION_VACIA)
        if i.producto_detalle.sku == 'SUBDIVISION_VACIA':
            continue

        data.append({
            'Rack': i.ubicacion.rack.nombre,
            'Nivel': i.ubicacion.nivel,
            'Posicion': i.ubicacion.posicion,
            'Ubicacion Completa': i.ubicacion.codigo_unico.split('-ID')[0],
            'SKU': i.producto_detalle.sku,
            'Descripcion': i.producto_detalle.descripcion,
            'Lote': i.lote,
            'Vencimiento': i.fecha_vencimiento,
            'Cantidad': i.cantidad,
            'Sub-Ubicacion': i.sub_ubicacion
        })

    if not data:
        flash("⚠️ No hay stock activo en el sector Logística para exportar.", "info")
        return redirect(url_for('logistica'))

    # Convertimos a DataFrame de Pandas
    df = pd.DataFrame(data)
    
    # Creamos el archivo en memoria
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventario Logistica')

    output.seek(0)
    
    fecha_hoy = datetime.now().strftime("%d-%m-%Y_%H%M")
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Stock_Logistica_{fecha_hoy}.xlsx'
    )

@app.route('/posventa')
@login_required
def posventa():
    # 1. Seguridad de roles
    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'tecnico', 'administrativo']:
        flash("Acceso denegado a Posventa.")
        return redirect(url_for('home'))
    
    # --- 🗑️ ELIMINAMOS EL BLOQUE "ZONAS_A_CREAR" QUE HACÍA QUE TODO REAPARECIERA ---

    # 2. Traemos todos los racks (Zonas y Estantes) de Posventa
    racks_posventa = Rack.query.filter_by(sector='posventa').all()
    
    # 3. --- CÁLCULOS DEL DASHBOARD (Mantenemos tu lógica intacta) ---
    total_pos = Ubicacion.query.join(Rack).filter(Rack.sector == 'posventa').count()
    
    # Contamos ubicaciones que tienen al menos un item con cantidad > 0
    ocupados_pos = db.session.query(Item.ubicacion_id)\
        .join(Ubicacion).join(Rack)\
        .filter(Rack.sector == 'posventa', Item.cantidad > 0)\
        .distinct().count()
        
    vacios_pos = total_pos - ocupados_pos
    
    porcentaje = round((ocupados_pos / total_pos) * 100, 1) if total_pos > 0 else 0
    
    # Lógica de color de alerta
    color_alerta = "#dc3545" if porcentaje >= 90 else "#6f42c1"

    # 4. Traemos productos, incidencias y notificaciones
    productos_pv = Producto.query.filter_by(sector='posventa').all()
    tickets_esperados = IncidenciaComercial.query.filter_by(estado='Abierto').all()
    
    # Notificaciones del taller (Equipos en estado Pendiente)
    reparaciones_pendientes = Reparacion.query.filter_by(estado='Pendiente').count()

    return render_template('posventa.html', 
                           racks=racks_posventa, 
                           total=total_pos, 
                           ocupados=ocupados_pos, 
                           vacios=vacios_pos, 
                           porcentaje=porcentaje,
                           color_alerta=color_alerta,
                           productos_posventa=productos_pv,
                           tickets_esperados=tickets_esperados,
                           notif_posventa=reparaciones_pendientes, # Agregamos esto para el circulito rojo del menú
                           datetime=datetime)

@app.route('/crear_rack_posventa', methods=['POST'])
@login_required
def crear_rack_posventa():

    if current_user.rol not in ['admin', 'jefe_logistica', 'jefe_posventa']:
        flash('⚠️ Acceso denegado.', 'error')
        return redirect(request.referrer)

    nombre = request.form.get('nombre').strip().upper()
    niveles = int(request.form.get('niveles'))
    posiciones = int(request.form.get('posiciones'))

    nuevo_rack = Rack(nombre=nombre, niveles=niveles, posiciones=posiciones, sector='posventa')
    db.session.add(nuevo_rack)
    db.session.commit()

    for n in range(1, niveles + 1):
        for p in range(1, posiciones + 1):
            # Mantenemos el PV- interno pero con el formato que ya conoces
            codigo = f"PV-{nombre}-{p}-{n}-ID{nuevo_rack.id}"
            nueva_ubi = Ubicacion(rack_id=nuevo_rack.id, nivel=n, posicion=p, codigo_unico=codigo)
            db.session.add(nueva_ubi)

    db.session.commit()
    return redirect(url_for('posventa'))   

# --- CARGA MASIVA: PRODUCTOS (CATÁLOGO) DESDE POSVENTA ---

@app.route('/importar_productos_posventa', methods=['POST'])
@login_required
def importar_productos_posventa():
    # 1. Verificamos que el archivo exista
    archivo = request.files.get('archivo_csv')
    if not archivo or archivo.filename == '':
        flash('❌ No se seleccionó ningún archivo.', 'error')
        return redirect(url_for('posventa'))

    try:
        # 2. Leemos el contenido y manejamos la codificación (igual que en Logística)
        raw_data = archivo.read()
        try:
            texto = raw_data.decode('utf-8-sig')
        except:
            texto = raw_data.decode('latin1')

        lineas = texto.splitlines()
        if len(lineas) <= 1:
            flash('⚠️ El archivo parece estar vacío.', 'error')
            return redirect(url_for('posventa'))

        # 3. Detectamos separador y preparamos el lector
        delimitador = ';' if ';' in lineas[0] else ','
        lector = csv.reader(lineas, delimiter=delimitador)
        next(lector, None) # Saltamos los títulos

        productos_agregados = 0
        productos_actualizados = 0
        errores_ean = 0

        # --- 🛡️ EL MURO DE BERLÍN: FILTRADO POR SECTOR POSVENTA ---
        # Solo traemos a la memoria los productos que ya son de 'posventa'
        todos_los_productos_pv = Producto.query.filter_by(sector='posventa').all()
        
        # Diccionario SKU -> Objeto (Solo del mundo Posventa)
        diccionario_sku = {p.sku: p for p in todos_los_productos_pv}
        
        # Set de EANs ya usados en Posventa para evitar duplicados internos
        eans_usados = {p.ean for p in todos_los_productos_pv if p.ean}

        for i, fila in enumerate(lector, start=2):
            if len(fila) == 1 and ',' in fila[0]:
                fila = fila[0].split(',')

            if len(fila) >= 3:
                # Normalizamos datos
                sku = str(fila[0]).strip().upper()
                ean = str(fila[1]).strip() if str(fila[1]).strip() else None
                desc = str(fila[2]).strip()
                modelo = str(fila[3]).strip()

                if sku == "": continue

                # ¿El producto ya existe en la nómina de POSVENTA?
                if sku in diccionario_sku:
                    producto_existente = diccionario_sku[sku]

                    # Validamos que el EAN no choque con otro producto de Posventa
                    if ean and ean != producto_existente.ean and ean in eans_usados:
                        errores_ean += 1
                        continue

                    # Actualizamos datos manteniendo el sector 'posventa'
                    producto_existente.ean = ean
                    producto_existente.descripcion = desc
                    if ean: eans_usados.add(ean)
                    productos_actualizados += 1

                else:
                    # NUEVO: No existe en Posventa
                    if ean and ean in eans_usados:
                        errores_ean += 1
                        continue

                    # 🚀 CREACIÓN CON SELLO DE POSVENTA
                    nuevo = Producto(
                        sku=sku, 
                        ean=ean, 
                        descripcion=desc,
                        modelo=modelo, 
                        sector='posventa' # El sello del muro
                    )
                    db.session.add(nuevo)

                    # Actualizamos la memoria local
                    diccionario_sku[sku] = nuevo
                    if ean: eans_usados.add(ean)
                    productos_agregados += 1

        # 4. Impactamos los cambios
        db.session.commit()
        flash(f'✅ Catálogo Posventa actualizado: {productos_agregados} nuevos, {productos_actualizados} actualizados. ⚠️ {errores_ean} EAN duplicados ignorados.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'❌ Error crítico al importar Posventa: {str(e)}', 'error')

    return redirect(url_for('posventa'))

# --- CARGA MASIVA: INVENTARIO (STOCK) SOLO PARA POSVENTA ---
@app.route('/importar_inventario_posventa', methods=['POST'])
@login_required
def importar_inventario_posventa():
    file = request.files.get('archivo_inventario')
    if not file:
        flash("❌ No se seleccionó ningún archivo.", "error")
        return redirect(url_for('posventa'))

    try:
        df = pd.read_excel(file)
        df.columns = [str(c).strip().lower().replace(' ', '').replace('-', '').replace('_', '').replace('ó', 'o') for c in df.columns]

        items_cargados = 0
        for i, row in df.iterrows():
            sku_val = str(row.get('sku', '')).strip().upper()
            
            # Limpiamos todos los espacios vacíos
            ubi_val = str(row.get('ubicacion', '')).strip().upper().replace(' ', '')
            if ubi_val.endswith('.0'): ubi_val = ubi_val[:-2]

            cant_val = row.get('cantidad', 0)
            sub_val = str(row.get('sububicacion', 'General')).strip()
            obs_val = str(row.get('observaciones', '')).strip()
            est_val = str(row.get('estado', 'no_apto')).strip().lower()

            if not sku_val or not ubi_val:
                continue

            ubi = None
            partes_ubi = ubi_val.split('-')
            
            # Si en el Excel le pusieron "PV-" adelante, lo ignoramos para no confundir
            if partes_ubi[0] == 'PV':
                partes_ubi.pop(0)

            # Si tiene 3 partes (Nombre - Nivel - Posición) o (Nombre - Pos - Niv)
            if len(partes_ubi) >= 3:
                r_nombre = partes_ubi[0]
                try:
                    # CORREGIDO: Asignación de Nivel y Posición
                    r_niv = int(partes_ubi[1]) 
                    r_pos = int(partes_ubi[2]) 
                    
                    ubi = Ubicacion.query.join(Rack).filter(
                        Rack.nombre == r_nombre,
                        Rack.sector == 'posventa',
                        Ubicacion.posicion == r_pos,
                        Ubicacion.nivel == r_niv
                    ).first()
                except ValueError:
                    pass

            # PLAN B: Si no lo encontró así, intenta la búsqueda vieja por texto
            if not ubi:
                ubi = Ubicacion.query.join(Rack).filter(
                    Ubicacion.codigo_unico.like(f"%{ubi_val}%"), 
                    Rack.sector == 'posventa'
                ).first()

            prod = Producto.query.filter_by(sku=sku_val, sector='posventa').first()

            if prod and ubi:
                nuevo_item = Item(
                    producto_id=prod.id, 
                    ubicacion_id=ubi.id, 
                    cantidad=int(cant_val), 
                    sub_ubicacion=sub_val, 
                    estado_calidad=est_val, 
                    observaciones=obs_val
                )
                db.session.add(nuevo_item)
                
                # 🔥 NUEVO: Ahora sí guarda en el historial de Posventa
                mov_historial = Movimiento(
                    tipo='ingreso',
                    sku=sku_val,
                    cantidad=int(cant_val),
                    origen="Carga Masiva (Excel)", 
                    transporte=ubi.codigo_unico.split('-ID')[0], # ACÁ USAMOS TRANSPORTE, NO DESTINO
                    usuario=current_user.username,
                    sector='posventa'
                )
                db.session.add(mov_historial)
                
                items_cargados += 1
            else:
                motivo = []
                if not prod: motivo.append(f"SKU '{sku_val}' no está en la nómina")
                if not ubi: motivo.append(f"Ubicación '{ubi_val}' no existe en el mapa")
                print(f"⚠️ Fila {i+2} ignorada: {' y '.join(motivo)}")

        db.session.commit()
        if items_cargados > 0:
            flash(f"✅ Se cargaron {items_cargados} lotes a Posventa y se registraron en el historial.", "success")
        else:
            flash("⚠️ No se cargó nada. Revisá la consola negra.", "error")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ ERROR: {str(e)}")
        flash(f"❌ Error al procesar: {str(e)}", "error")

    return redirect(url_for('posventa'))

@app.route('/descargar_plantilla_posventa')
@login_required
def descargar_plantilla_posventa():
    # 🔥 Agregamos modelo y observaciones
    columnas = ['sku', 'modelo', 'ubicacion', 'cantidad', 'sub-ubicacion', 'observaciones', 'estado']
    ejemplo = {
        'sku': 'SKU123',
        'modelo': 'MODELO-XP',
        'ubicacion': 'PV-RACK1-1-1',
        'cantidad': 1,
        'sub-ubicacion': 'Caja 1',
        'observaciones': 'Carcasa partida',
        'estado': 'no_apto'
    }
    df = pd.DataFrame([ejemplo], columns=columnas)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Plantilla Stock')
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='Plantilla_Stock_PV.xlsx')


@app.route('/descargar_plantilla_catalogo')
@login_required
def descargar_plantilla_catalogo():
    import pandas as pd
    import io
    from flask import send_file

    columnas = ['SKU', 'DESCRIPCION', 'EMPRESA', 'EAN', 'FAMILIA', 
                'ALTO (cm)', 'ANCHO (cm)', 'PROFUNDIDAD (cm)', 
                'UNIDADES X BULTO', 'BULTOS X PISO', 'PISOS X PALLET', 'BULTOS X PALLET']
    
    ejemplo = {
        'SKU': 'CORT0001', 'DESCRIPCION': 'CORTINA ROLLER BLACKOUT 120X150', 'EMPRESA': 'MI EMPRESA', 
        'EAN': '7790000000000', 'FAMILIA': 'CORTINAS', 
        'ALTO (cm)': 150.0, 'ANCHO (cm)': 120.0, 'PROFUNDIDAD (cm)': 5.5, 
        'UNIDADES X BULTO': 4, 'BULTOS X PISO': 10, 'PISOS X PALLET': 5, 'BULTOS X PALLET': 50
    }
    
    df = pd.DataFrame([ejemplo], columns=columnas)
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Plantilla Nomina')
        # Auto-ajuste de columnas
        worksheet = writer.sheets['Plantilla Nomina']
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length: max_length = len(cell.value)
                except: pass
            worksheet.column_dimensions[column].width = min(max_length + 2, 40)

    output.seek(0)
    return send_file(output, download_name="plantilla_nomina_logistica.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/eliminar_producto/<int:producto_id>', methods=['POST'])
@login_required
def eliminar_producto(producto_id):

    if current_user.rol not in ['admin', 'jefe_logistica']:
        flash("⚠️ No tenés permisos para realizar esta acción.", "error")
        return redirect(request.referrer)


    # 1. Buscamos el producto
    producto = Producto.query.get_or_404(producto_id)

    # 2. Verificamos si tiene stock antes de borrar
    tiene_stock = Item.query.filter_by(producto_id=producto_id).first()
    
    if tiene_stock:
        flash(f"❌ No se puede borrar '{producto.sku}' porque tiene stock físico.", "error")
        return redirect(request.referrer)

    # 3. Si no tiene stock, procedemos al borrado
    try:
        db.session.delete(producto)
        db.session.commit()
        flash(f"✅ Producto '{producto.sku}' eliminado correctamente.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al eliminar: {str(e)}", "error")

    return redirect(request.referrer or url_for('home'))


@app.route('/posventa/nomina')
@login_required
def nomina_posventa():
    roles_permitidos = ['admin', 'posventa', 'administrativo', 'jefe_posventa', 'comercial', 'gerencia']
    if current_user.rol.lower() not in roles_permitidos:
        flash("No tienes permiso para ver esta sección.", "error")
        return redirect(url_for('home'))

    origen = request.args.get('origen', 'posventa')
    q = request.args.get('q', '').strip().upper()
    
    # 🔥 CAPTURAMOS EL NÚMERO DE PÁGINA (Por defecto la 1)
    page = request.args.get('page', 1, type=int)

    query = Producto.query.filter(
        Producto.sector == 'posventa', 
        Producto.sku != 'SUBDIVISION_VACIA'
    )

    if q:
        busqueda_rapida = f"{q}%"
        query = query.filter(
            db.or_(
                Producto.sku.like(busqueda_rapida),
                Producto.descripcion.ilike(f"%{q}%")
            )
        )

    # 🚀 APLICAMOS PAGINACIÓN: 100 productos por página
    # Esto devuelve un objeto 'pagination' en lugar de una lista simple
    pagination = query.order_by(Producto.sku.asc()).paginate(page=page, per_page=100, error_out=False)
    
    return render_template('nomina_posventa.html', 
                           productos=pagination.items, # Los productos de la página actual
                           pagination=pagination,      # El objeto con los datos de las páginas
                           origen=origen, 
                           q=q)

@app.route('/materias_primas/vaciar_nomina', methods=['POST'])
@login_required
def vaciar_nomina_materias_primas():
    # 🔥 ESCUDO DE SÚPER-USUARIO
    rol_limpio = current_user.rol.strip().lower() if current_user.rol else ""
    
    if rol_limpio != 'admin':
        flash("🚫 Acceso denegado: Solo un administrador general puede vaciar el catálogo maestro.", "error")
        return redirect(request.referrer)

    # 1. Buscamos solo los productos que pertenecen a Materias Primas
    # EXCLUIMOS la subdivisión vacía para no romper los estantes
    productos_mp = Producto.query.filter(
        Producto.sector == 'materias_primas',
        Producto.sku != 'SUBDIVISION_VACIA'
    ).all()
    
    ids_productos = [p.id for p in productos_mp]

    if not ids_productos:
        flash("La nómina ya está vacía.", "info")
        return redirect(request.referrer)

    # 2. 🛡️ CHEQUEO DE STOCK SECTORIZADO
    tiene_stock_en_mp = Item.query.join(Ubicacion).join(Rack).filter(
        Item.producto_id.in_(ids_productos),
        Item.cantidad > 0,
        Rack.sector == 'materias_primas'
    ).first()

    if tiene_stock_en_mp:
        flash("❌ No se puede vaciar: Hay insumos con stock físico en los racks. Primero poné el stock en 0.", "error")
        return redirect(request.referrer)

    # 3. Borrado seguro
    try:
        Item.query.filter(Item.producto_id.in_(ids_productos)).delete(synchronize_session=False)
        Producto.query.filter(Producto.sector == 'materias_primas', Producto.sku != 'SUBDIVISION_VACIA').delete(synchronize_session=False)
        
        db.session.commit()
        flash("✅ Nómina de Materias Primas vaciada correctamente.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error técnico: {str(e)}", "error")

    return redirect(request.referrer)


@app.route('/logistica/nomina')
@login_required
def nomina_logistica():
    if current_user.rol not in ['admin', 'stock', 'jefe_logistica', 'ruteador', 'Ruteador', 'operario', 'operario_logistica', 'consultas']:
        flash('⚠️ No tienes permiso para ver el catálogo.', 'error')
        return redirect(url_for('home'))
        
    origen = request.args.get('origen', 'logistica')
    
    # 🔍 CAPTURAMOS EL TÉRMINO DE BÚSQUEDA Y PÁGINA
    q = request.args.get('q', '').strip().upper()
    page = request.args.get('page', 1, type=int)

    # Base de la consulta: Solo Logística y sin fantasmas
    query = Producto.query.filter(
        Producto.sector == 'logistica', 
        Producto.sku != 'SUBDIVISION_VACIA'
    )

    # 🚀 SI BUSCÓ ALGO, FILTRAMOS
    if q:
        busqueda_rapida = f"{q}%"
        query = query.filter(
            db.or_(
                Producto.sku.like(busqueda_rapida),
                Producto.descripcion.ilike(f"%{q}%")
            )
        )

    # 🚀 PAGINACIÓN: 100 productos por página
    pagination = query.order_by(Producto.sku.asc()).paginate(page=page, per_page=100, error_out=False)
    
    return render_template('nomina_logistica.html', 
                           productos=pagination.items, 
                           pagination=pagination, 
                           origen=origen, 
                           q=q)



# --- VACIAR NÓMINA LOGÍSTICA ---
@app.route('/logistica/vaciar_nomina', methods=['POST'])
@login_required
def vaciar_nomina_logistica():
    if current_user.rol not in ['admin', 'jefe_logistica']:
        flash("⚠️ Solo un administrador o Jefe puede hacer esto.", "error")
        return redirect(request.referrer)

    productos_log = Producto.query.filter_by(sector='logistica').all()
    ids = [p.id for p in productos_log]
    
    # Verificamos si hay stock físico en racks de logística
    tiene_stock = Item.query.filter(Item.producto_id.in_(ids)).first()

    if tiene_stock:
        flash("❌ No se puede vaciar: Aún hay stock en los racks de Logística.", "error")
        return redirect(url_for('nomina_logistica'))

    try:
        Producto.query.filter_by(sector='logistica').delete()
        db.session.commit()
        flash("✅ Nómina de Logística vaciada correctamente.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error: {str(e)}", "error")
    return redirect(url_for('nomina_logistica'))

@app.route('/marcar_como_reparado/<int:item_id>', methods=['POST'])
@login_required
def marcar_como_reparado(item_id):
    item = Item.query.get_or_404(item_id)
    producto = Producto.query.get(item.producto_id)
    ubicacion = Ubicacion.query.get(item.ubicacion_id)
    rack = Rack.query.get(ubicacion.rack_id)

    # 1. Cambiamos el estado en la tabla Item
    item.estado_calidad = 'apto'

    # 2. AQUÍ VA EL BLOQUE: Registramos el movimiento en el historial
    nuevo_movimiento = Movimiento(
        tipo='ajuste', 
        sku=producto.sku,
        cantidad=item.cantidad,
        origen=f"{rack.nombre}-{ubicacion.posicion}-{ubicacion.nivel}",
        transporte="🔧 Revisión: Pasó de NO APTO a APTO", 
        usuario=current_user.username,
        fecha=hora_argentina()
    )
    db.session.add(nuevo_movimiento)

    # 3. Guardamos todo junto
    db.session.commit()
    flash(f"✅ {producto.sku} marcado como APTO y registrado en historial.", "success")
    return redirect(request.referrer)


@app.route('/revisar_producto_posventa/<int:item_id>', methods=['POST'])
@login_required
def revisar_producto_posventa(item_id):
    item = Item.query.get_or_404(item_id)
    
    # 1. Cambiamos el estado
    item.estado_calidad = 'apto'
    
    # 📝 2. REGISTRO EN EL HISTORIAL
    nuevo_ajuste = Movimiento(
        tipo='ajuste',
        sku=item.producto_detalle.sku,
        cantidad=item.cantidad,
        origen=item.ubicacion.codigo_unico.split('-ID')[0],
        transporte="🔧 REVISIÓN TÉCNICA: Pasó a APTO",
        usuario=current_user.username,
        sector='posventa'
    )
    db.session.add(nuevo_ajuste)
    
    db.session.commit()
    flash(f"✅ SKU {item.producto_detalle.sku} revisado y listo para despacho.", "success")
    return redirect(request.referrer)

@app.route('/historial/<sector>')
@login_required
def ver_historial(sector):
    # Solo traemos los ajustes del sector solicitado
    ajustes = HistorialAjuste.query.filter_by(sector=sector).order_by(HistorialAjuste.fecha.desc()).all()
    
    # Definimos el color según el sector para la interfaz
    color = "#0d6efd" if sector == "logistica" else "#6f42c1"
    titulo = "Logística" if sector == "logistica" else "Posventa"
    
    return render_template('historial.html', ajustes=ajustes, sector=sector, color=color, titulo=titulo)

import io
import pandas as pd
from flask import send_file

@app.route('/descargar_plantilla_inventario')
@login_required
def descargar_plantilla_inventario():
    # 🔥 1. Definimos TODAS las columnas que nuestro sistema acepta
    columnas = ['SKU', 'Modelo', 'Ubicacion', 'Lote', 'Vencimiento (DD/MM/AAAA)', 'Cantidad', 'Sub-Ubicacion', 'Estado', 'Observaciones']
    
    # 🔥 2. Creamos una fila de ejemplo para "educar" al operario
    ejemplo = [{
        'SKU': 'TEST-123',
        'Modelo': 'Ejemplo Modelo',
        'Ubicacion': 'A-01-01',
        'LOTE': 'L1',
        'VENCIMIENTO': '01/01/2000',
        'Cantidad': 15,
        'Sub-Ubicacion': 'General',
        'Estado': 'apto', # Aclaramos en el texto que puede ser apto, outlet o no apto
        'Observaciones': 'Ejemplo: apto / outlet / no_apto'
    }]
    
    # Creamos el DataFrame con la fila de ejemplo y las columnas
    df = pd.DataFrame(ejemplo, columns=columnas)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Stock_Logistica')
        
        # (Opcional) Le damos un poco de ancho a las columnas para que se lea bien el ejemplo
        worksheet = writer.sheets['Stock_Logistica']
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter # Obtenemos la letra de la columna
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(cell.value)
                except:
                    pass
            adjusted_width = (max_length + 2)
            worksheet.column_dimensions[column].width = adjusted_width

    output.seek(0)
    
    return send_file(
        output, 
        download_name="Plantilla_Stock_Logistica.xlsx", 
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route('/buscar_productos_sugeridos')
@login_required
def buscar_productos_sugeridos():
    query = request.args.get('q', '').strip()
    # 🔥 Capturamos el sector que viene desde el HTML
    sector_actual = request.args.get('sector', 'logistica') 
    
    # Preparamos la base de la búsqueda: 
    if sector_actual == 'posventa':
        # En Posventa permitimos buscar tanto equipos como repuestos
        filtro_base = [
            Producto.sector.in_(['posventa', 'repuestos']),
            Producto.sku != 'SUBDIVISION_VACIA'
        ]
    else:
        filtro_base = [
            Producto.sector == sector_actual,
            Producto.sku != 'SUBDIVISION_VACIA'
        ]

    if not query:
        # Si el buscador está vacío, mostramos los primeros 50 del catálogo limpio
        productos = Producto.query.filter(*filtro_base).order_by(Producto.sku.asc()).all()
    else:
        # Si el usuario escribe, filtramos por SKU o Descripción
        productos = Producto.query.filter(
            *filtro_base,
            db.or_(
                Producto.sku.ilike(f'%{query}%'),
                Producto.descripcion.ilike(f'%{query}%')
            )
        ).limit(50).all()

    return jsonify([{"id": p.id, "sku": p.sku, "nombre": p.descripcion} for p in productos])

@app.route('/eliminar_item_busqueda/<int:item_id>', methods=['POST'])
@login_required
def eliminar_item_busqueda(item_id):
    # Seguridad: Solo niveles altos pueden borrar físicamente un registro
    if current_user.rol not in ['admin', 'jefe_logistica', 'jefe_posventa']:
        flash("⚠️ No tienes permisos para eliminar stock permanentemente.", "error")
        return redirect(request.referrer)

    item = Item.query.get_or_404(item_id)

    # 🔥 CORRECCIÓN: Usamos 'producto_detalle' y 'ubicacion' que son tus nombres reales
    sku_afectado = item.producto_detalle.sku
    cant_afectada = item.cantidad
    ubi_afectada = item.ubicacion.codigo_unico

    try:
        # 📝 REGISTRAMOS EN EL HISTORIAL
        nuevo_log = Movimiento(
            tipo='ajuste', 
            sku=sku_afectado,
            cantidad=cant_afectada,
            origen=f"BORRADO DESDE BUSQUEDA - Ubic: {ubi_afectada}",
            transporte="SISTEMA",
            usuario=current_user.username,
            sector=item.ubicacion.rack.sector # Usamos el sector del rack de esa ubicación
        )
        db.session.add(nuevo_log)
        
        # Borramos el registro
        db.session.delete(item)
        db.session.commit()
        
        flash(f"🗑️ El ítem {sku_afectado} fue eliminado de la ubicación {ubi_afectada}.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al eliminar: {str(e)}", "error")

    return redirect(request.referrer)

# ==========================================
# RUTAS DEL TALLER (POSVENTA)
# ==========================================

@app.route('/taller')
@login_required
def taller_posventa():
    # ... seguridad de roles ...

    # 🔍 BUSCAMOS SI EL TÉCNICO TIENE UNA TAREA ACTIVA
    tarea_actual = Reparacion.query.filter_by(
        tecnico=current_user.username, 
        estado='En revisión'
    ).first()

    # Traemos el resto de los pendientes (excluimos la que ya tiene el técnico para no duplicar)
    equipos_taller = Reparacion.query.filter(
        Reparacion.estado.in_(['Pendiente', 'Pausado', 'En revisión']), 
        Reparacion.sector == 'posventa'
    ).all()

    return render_template('taller.html', pendientes=equipos_taller, tarea_actual=tarea_actual)


@app.route('/iniciar_reparacion/<int:rep_id>', methods=['POST'])
@login_required
def iniciar_reparacion(rep_id):
    # 0. Control de acceso
    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'tecnico']:
        flash("⚠️ Acceso denegado.", "error")
        return redirect(url_for('home'))

    # 1. PRIMERO: Definimos la variable (Buscamos el objeto en la DB)
    # Sin esta línea arriba de todo, Python no sabe qué es 'reparacion_objetivo'
    reparacion_objetivo = Reparacion.query.get_or_404(rep_id)

    # 2. SEGUNDO: Grabamos la fecha de inicio real (solo si está vacía)
    # Ahora que ya sabemos qué es 'reparacion_objetivo', podemos preguntarle cosas
    if not reparacion_objetivo.fecha_primer_inicio:
        reparacion_objetivo.fecha_primer_inicio = datetime.now()

    # 3. CANDADO: ¿Es mi tarea actual? (Paso directo)
    if reparacion_objetivo.estado == 'En revisión' and reparacion_objetivo.tecnico == current_user.username:
        return redirect(url_for('pantalla_reparacion_activa', rep_id=reparacion_objetivo.id))

    # 4. CANDADO: ¿Es de otro?
    if reparacion_objetivo.estado == 'En revisión' and reparacion_objetivo.tecnico != current_user.username:
        flash(f"🚫 Ocupado por {reparacion_objetivo.tecnico}", "error")
        return redirect(url_for('taller_posventa'))

    # 5. CANDADO: ¿Tengo otra tarea abierta?
    otra_activa = Reparacion.query.filter(
        Reparacion.tecnico == current_user.username,
        Reparacion.estado == 'En revisión',
        Reparacion.id != rep_id
    ).first()

    if otra_activa:
        flash(f"⚠️ Ya estás trabajando en {otra_activa.sku}. Terminá esa primero.", "error")
        return redirect(url_for('taller_posventa'))

    # --- 6. TODO OK: Iniciamos ---
    reparacion_objetivo.tecnico = current_user.username
    reparacion_objetivo.estado = 'En revisión'
    reparacion_objetivo.fecha_inicio_reparacion = datetime.now()
    
    db.session.commit()
    
    return redirect(url_for('pantalla_reparacion_activa', rep_id=reparacion_objetivo.id))

@app.route('/reparacion_activa/<int:rep_id>')
@login_required
def pantalla_reparacion_activa(rep_id):
    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'tecnico']:
        flash("⚠️ Acceso denegado.", "error")
        return redirect(url_for('home'))

    reparacion = Reparacion.query.get_or_404(rep_id)
    
    # 🔥 EL ARREGLO ESTÁ ACÁ: Cambiamos 'En proceso' por 'En revisión'
    if reparacion.tecnico != current_user.username or reparacion.estado != 'En revisión':
        return redirect(url_for('taller_posventa'))

    if not reparacion.fecha_inicio_reparacion:
        reparacion.fecha_inicio_reparacion = datetime.now()
        db.session.commit()

    segundos_transcurridos = int((datetime.now() - reparacion.fecha_inicio_reparacion).total_seconds())

    return render_template('reparacion_activa.html', reparacion=reparacion, segundos_inicio=segundos_transcurridos)

@app.route('/finalizar_reparacion/<int:rep_id>', methods=['POST'])
@login_required
def finalizar_reparacion(rep_id):
    # 🔒 Candado de seguridad de roles
    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'tecnico']:
        return redirect(url_for('home'))

    reparacion = Reparacion.query.get_or_404(rep_id)

    # 🛡️ CANDADO DE ESTADO: Validamos que sea el técnico correcto y el estado correcto
    if reparacion.tecnico != current_user.username or reparacion.estado != 'En revisión':
        flash("⚠️ No puedes finalizar una reparación que no tienes activa.", "error")
        return redirect(url_for('taller_posventa'))

    # 1. Capturamos los datos del formulario
    diagnostico = request.form.get('diagnostico')
    repuestos = request.form.get('repuestos', '')
    resolucion = request.form.get('resolucion_calidad')

    # 🔥 CALCULAR TIEMPO FINAL (FIX ZONAS HORARIAS) 🔥
    if reparacion.fecha_inicio_reparacion:
        # Le sacamos la zona horaria a ambas fechas para poder restarlas en paz
        ahora_naive = hora_argentina().replace(tzinfo=None)
        inicio_naive = reparacion.fecha_inicio_reparacion.replace(tzinfo=None)
        
        duracion_final = (ahora_naive - inicio_naive).total_seconds()
        
        if not reparacion.tiempo_acumulado:
            reparacion.tiempo_acumulado = 0
        reparacion.tiempo_acumulado += int(duracion_final)

    # 2. Actualizamos la Orden
    reparacion.diagnostico = diagnostico
    reparacion.repuestos = repuestos
    reparacion.resolucion_calidad = resolucion
    reparacion.estado = 'Finalizado'
    reparacion.fecha_fin = datetime.now()
    reparacion.fecha_inicio_reparacion = None # Apagamos el reloj

    producto = Producto.query.filter_by(sku=reparacion.sku, sector='posventa').first()
    texto_transporte = ""

    # =========================================================
    # 3. DESTINO INTELIGENTE (BÚSQUEDA DINÁMICA POR PROPÓSITO)
    # =========================================================
    
    if resolucion == 'apto' and producto:
        # Buscamos la zona con propósito APTO
        rack_destino = Rack.query.filter_by(proposito='APTO', sector='posventa').first()
        
        if not rack_destino or not rack_destino.ubicaciones:
            db.session.rollback()
            flash("❌ Error: No configuraste ninguna zona con propósito 'APTO'. Creala en Ajustes primero.", "error")
            return redirect(url_for('taller_posventa'))
            
        ubi_destino = rack_destino.ubicaciones[0]

        # 🔥 LÓGICA DE SUMAR STOCK (Para que Logística vea totales):
        item_existente = Item.query.filter_by(
            ubicacion_id=ubi_destino.id, 
            producto_id=producto.id, 
            estado_calidad='apto',
            sub_ubicacion='General'
        ).first()

        if item_existente:
            item_existente.cantidad += reparacion.cantidad
            item_existente.observaciones = "Productos reparados listos para stock"
        else:
            nuevo_item = Item(
                ubicacion_id=ubi_destino.id, 
                producto_id=producto.id, 
                cantidad=reparacion.cantidad,
                sub_ubicacion='General', 
                estado_calidad='apto',
                observaciones="Productos reparados listos para stock"
            )
            db.session.add(nuevo_item)
            
        texto_transporte = f"A {rack_destino.nombre} (SUMADO AL STOCK)"

    elif resolucion == 'outlet' and producto:
        # Buscamos la zona con propósito OUTLET
        rack_destino = Rack.query.filter_by(proposito='OUTLET', sector='posventa').first()
        
        if not rack_destino or not rack_destino.ubicaciones:
            db.session.rollback()
            flash("❌ Error: No configuraste ninguna zona con propósito 'OUTLET'. Creala en Ajustes primero.", "error")
            return redirect(url_for('taller_posventa'))
            
        ubi_destino = rack_destino.ubicaciones[0]

        nuevo_item = Item(
            ubicacion_id=ubi_destino.id, producto_id=producto.id, cantidad=reparacion.cantidad,
            sub_ubicacion=f"REP-{reparacion.id}", estado_calidad='outlet',
            observaciones=f"Reparado (Outlet): {diagnostico[:45]}"
        )
        db.session.add(nuevo_item)
        texto_transporte = f"A {rack_destino.nombre} (OUTLET)"

    elif resolucion == 'no_apto' and producto:
        # Intenta volver al origen de donde vino
        ubi_volver = Ubicacion.query.filter(Ubicacion.codigo_unico.like(f"{reparacion.ubicacion_origen}%")).first()
        
        # Si no puede volver al origen, busca la zona NO APTO nueva que creamos hoy
        if not ubi_volver:
            rack_destino = Rack.query.filter_by(proposito='NO APTO', sector='posventa').first()
            if not rack_destino or not rack_destino.ubicaciones:
                db.session.rollback()
                flash("❌ Error: No existe ubicación de origen ni Zona 'NO APTO' configurada para dejar este equipo.", "error")
                return redirect(url_for('taller_posventa'))
            ubi_volver = rack_destino.ubicaciones[0]

        if ubi_volver:
            nuevo_item = Item(
                ubicacion_id=ubi_volver.id, producto_id=producto.id, cantidad=reparacion.cantidad,
                sub_ubicacion=f"REP-{reparacion.id}", estado_calidad='no_apto',
                observaciones=f"Rechazado en taller. Falla: {reparacion.falla_reportada}"
            )
            db.session.add(nuevo_item)
            texto_transporte = f"A {ubi_volver.rack.nombre} (NO APTO)"

    elif resolucion == 'desguace':
        texto_transporte = "DESGUACE (BAJA DE STOCK)"

    # 4. REGISTRO EN EL HISTORIAL
    log_mov = Movimiento(
        tipo='ajuste' if resolucion == 'desguace' else 'movimiento',
        sku=reparacion.sku,
        cantidad=reparacion.cantidad,
        origen="TALLER TÉCNICO",
        transporte=texto_transporte, 
        usuario=current_user.username,
        sector='posventa'
    )
    db.session.add(log_mov)
    db.session.commit()
    
    flash(f"✅ Reparación finalizada. Destino: {texto_transporte}", "success")
    return redirect(url_for('taller_posventa'))
    
@app.route('/enviar_taller/<int:item_id>', methods=['POST'])
@login_required
def enviar_taller(item_id):
    # 1. Seguridad de roles
    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa']:
        flash("⚠️ No tienes permisos.", "error")
        return redirect(request.referrer)

    item = Item.query.get_or_404(item_id)
    
    # 🔥 FIX DE SEGURIDAD: Validamos que el producto exista en el catálogo antes de seguir
    producto = Producto.query.get(item.producto_id)
    
    if not producto:
        flash(f"❌ Error crítico: El ítem ID {item.id} está huérfano (no tiene producto asociado). Por favor, ajustá el stock o borrá la subdivisión.", "error")
        return redirect(request.referrer)

    try:
        cantidad_enviar = int(request.form.get('cantidad_taller', 1))
    except (ValueError, TypeError):
        flash("❌ Cantidad inválida.", "error")
        return redirect(request.referrer)

    if cantidad_enviar > item.cantidad or cantidad_enviar <= 0:
        flash("❌ Cantidad inválida o insuficiente.", "error")
        return redirect(request.referrer)

    # 🔥 LA MAGIA ACÁ: Validamos que el PROPÓSITO de la zona sea TALLER
    if item.ubicacion.rack.proposito != 'TALLER':
        flash(f"⚠️ Los productos deben estar en una zona configurada como TALLER para enviarlos a reparar. Este estante está configurado como '{item.ubicacion.rack.proposito or 'Sin definir'}'.", "error")
        return redirect(request.referrer)

    # Ahora sí usamos el producto que ya sabemos que existe
    sku_enviado = producto.sku
    ubi_origen = item.ubicacion.codigo_unico.split('-ID')[0]
    falla = item.observaciones

    # Registramos las reparaciones individuales
    for _ in range(cantidad_enviar):
        nueva_orden = Reparacion(
            sku=sku_enviado,
            cantidad=1, 
            ubicacion_origen=ubi_origen,
            tecnico="Sin asignar",
            falla_reportada=falla,
            estado='Pendiente',
            sector='posventa'
        )
        db.session.add(nueva_orden)

    # Descontamos el stock
    item.cantidad -= cantidad_enviar
    if item.cantidad <= 0:
        # Si era una caja (subdivisión), la convertimos en fantasma vacío para no borrarla
        if item.sub_ubicacion != 'General':
            prod_vacio = Producto.query.filter_by(sku='SUBDIVISION_VACIA', sector='posventa').first()
            if prod_vacio:
                item.producto_id = prod_vacio.id
                item.cantidad = 0
                item.estado_calidad = 'vacia'
                item.observaciones = 'Caja libre esperando mercadería'
            else:
                db.session.delete(item)
        else:
            db.session.delete(item)

    # Registro en historial
    log_mov = Movimiento(
        tipo='movimiento',
        sku=sku_enviado,
        cantidad=cantidad_enviar,
        origen=ubi_origen,
        transporte=f"ENVÍO A TALLER ({cantidad_enviar}u)", 
        usuario=current_user.username,
        sector='posventa'
    )
    db.session.add(log_mov)

    db.session.commit()
    flash(f"🛠️ Se enviaron {cantidad_enviar} unidades de {sku_enviado} al taller.", "success")
    return redirect(request.referrer)

from datetime import datetime

@app.route('/abandonar_reparacion/<int:rep_id>')
@login_required
def abandonar_reparacion(rep_id):

    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'tecnico']:
        flash("⚠️ Acceso denegado.", "error")
        return redirect(url_for('home'))

    reparacion = Reparacion.query.get_or_404(rep_id)
    
    if reparacion.tecnico == current_user.username:
        reparacion.estado = 'Pendiente'
        reparacion.tecnico = 'Sin asignar'
        reparacion.fecha_inicio_reparacion = None # Reseteamos el reloj
        db.session.commit()
        flash("Reparación liberada. Volvió a la lista de pendientes.", "info")
        
    return redirect(url_for('taller_posventa'))

@app.route('/cancelar_reparacion/<int:rep_id>')
@login_required
def cancelar_reparacion(rep_id):


    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'tecnico']:
        flash("⚠️ Acceso denegado.", "error")
        return redirect(url_for('home'))

    reparacion = Reparacion.query.get_or_404(rep_id)
    # Reset total
    reparacion.estado = 'Pendiente'
    reparacion.tecnico = 'Sin asignar'
    reparacion.fecha_inicio_reparacion = None
    reparacion.tiempo_acumulado = 0 
    db.session.commit()
    flash("🚫 Reparación cancelada. El equipo volvió a pendientes.", "info")
    return redirect(url_for('taller_posventa'))

@app.route('/pausar_reparacion/<int:rep_id>')
@login_required
def pausar_reparacion(rep_id):
    # 1. Seguridad de roles
    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'tecnico']:
        flash("⚠️ Acceso denegado.", "error")
        return redirect(url_for('home'))

    reparacion = Reparacion.query.get_or_404(rep_id)

    # 🛡️ BLOQUEO DE SEGURIDAD: 
    # Solo pausamos si el estado es 'En revisión' y el técnico es el dueño
    if reparacion.estado == 'En revisión' and reparacion.fecha_inicio_reparacion:
        
        # Validamos que sea el mismo técnico que inició la tarea
        if reparacion.tecnico != current_user.username:
            flash("🚫 No puedes pausar una reparación iniciada por otro técnico.", "error")
            return redirect(url_for('taller_posventa'))

        # Calculamos cuánto tiempo pasó en esta sesión
        ahora = datetime.now()
        duracion_sesion = (ahora - reparacion.fecha_inicio_reparacion).total_seconds()
        
        # Sumamos al acumulado
        if not reparacion.tiempo_acumulado:
            reparacion.tiempo_acumulado = 0
        
        reparacion.tiempo_acumulado += int(duracion_sesion)
        
        # Cambiamos estado y liberamos el reloj de sesión
        reparacion.estado = 'Pausado'
        reparacion.fecha_inicio_reparacion = None 
        
        db.session.commit()
        flash("☕ Reparación pausada. El tiempo se guardó correctamente.", "success")
    else:
        # Si ya estaba pausado o el estado era otro (por usar el botón atrás)
        flash("ℹ️ La reparación ya se encuentra pausada o no está activa.", "info")
    
    return redirect(url_for('taller_posventa'))


@app.route('/historial_taller')
@login_required
def historial_taller():
    # 1. Seguridad: Solo admin y jefe pueden ver el rendimiento
    if current_user.rol not in ['admin', 'jefe_posventa']:
        flash("⚠️ Acceso denegado. No tienes permisos para ver el historial.", "error")
        return redirect(url_for('taller_posventa'))

    # 2. Capturamos los filtros desde la URL (si no existen, quedan vacíos)
    tecnico_filtro = request.args.get('tecnico', '')
    fecha_filtro = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    sku_filtro = request.args.get('sku', '')  # <-- Nuevo filtro de producto

    # 3. Base de la consulta: Solo lo finalizado en el sector posventa
    query = Reparacion.query.filter_by(estado='Finalizado', sector='posventa')

    # 4. Aplicamos los filtros dinámicamente
    if tecnico_filtro:
        query = query.filter_by(tecnico=tecnico_filtro)
    
    if fecha_filtro:
        # Filtramos por el día de la fecha de finalización
        query = query.filter(db.func.date(Reparacion.fecha_fin) == fecha_filtro)
    
    if sku_filtro:
        # Buscamos coincidencias que CONTENGAN el texto ingresado (ignora mayúsculas/minúsculas en la mayoría de DBs)
        query = query.filter(Reparacion.sku.contains(sku_filtro))

    # 5. Ordenamos por lo más reciente y ejecutamos la consulta
    reparaciones = query.order_by(Reparacion.fecha_fin.desc()).all()

    # 6. Datos auxiliares para la vista
    # Sacamos la lista de técnicos únicos que han reparado algo para llenar el desplegable
    lista_tecnicos = db.session.query(Reparacion.tecnico).filter(Reparacion.tecnico != None).distinct().all()
    tecnicos_lista = [t[0] for t in lista_tecnicos]

    # Calculamos el total de unidades del listado actual (filtrado)
    total_reparado = sum(r.cantidad for r in reparaciones)

    return render_template('historial_reparaciones.html', 
                           reparaciones=reparaciones, 
                           tecnicos=tecnicos_lista,
                           tecnico_sel=tecnico_filtro,
                           fecha_sel=fecha_filtro,
                           sku_sel=sku_filtro, # Enviamos el valor para que el buscador no se vacíe
                           total_dia=total_reparado)

@app.route('/devolver_sin_reparar/<int:rep_id>', methods=['POST'])
@login_required
def devolver_sin_reparar(rep_id):
    # 🔥 CANDADO ESTRICTO: Solo Jefatura y Admin
    if current_user.rol not in ['admin', 'jefe_posventa']:
        flash("⚠️ Acción reservada exclusivamente para Jefatura.", "error")
        return redirect(url_for('taller_posventa'))

    reparacion = Reparacion.query.get_or_404(rep_id)
    producto = Producto.query.filter_by(sku=reparacion.sku, sector='posventa').first()
    
    # 1. DEVOLUCIÓN AL RACK FÍSICO COMO "NO APTO"
    if producto:
        ubi_volver = Ubicacion.query.filter(Ubicacion.codigo_unico.like(f"{reparacion.ubicacion_origen}%")).first()
        if ubi_volver:
            item_existente = Item.query.filter_by(
                ubicacion_id=ubi_volver.id,
                producto_id=producto.id,
                estado_calidad='no_apto'
            ).first()
            
            if item_existente:
                item_existente.cantidad += reparacion.cantidad
            else:
                nuevo_item = Item(
                    ubicacion_id=ubi_volver.id,
                    producto_id=producto.id,
                    cantidad=reparacion.cantidad,
                    estado_calidad='no_apto',
                    observaciones=f"Devuelto sin reparar. Falla reportada: {reparacion.falla_reportada}"
                )
                db.session.add(nuevo_item)
                
    # 2. CIERRE DE LA ORDEN EN EL TALLER
    reparacion.estado = 'Finalizado'
    reparacion.resolucion_calidad = 'no_apto'  # 🔥 Nuevo estado final
    reparacion.diagnostico = 'Rechazado/Devuelto a rack sin reparar (Acción de Jefatura)'
    reparacion.tecnico = current_user.username
    reparacion.fecha_fin = datetime.now()
    
    # 3. REGISTRO EN EL HISTORIAL GENERAL
    log_mov = Movimiento(
        tipo='movimiento',
        sku=reparacion.sku,
        cantidad=reparacion.cantidad,
        origen="TALLER TÉCNICO",
        transporte=f"DEVUELTO A {reparacion.ubicacion_origen} (NO APTO)", 
        usuario=current_user.username,
        sector='posventa'
    )
    db.session.add(log_mov)
    db.session.commit()
    
    flash(f"🔙 El equipo {reparacion.sku} fue retirado del Taller y volvió al rack como NO APTO.", "info")
    return redirect(url_for('taller_posventa'))

@app.route('/mover_item/<int:item_id>', methods=['POST'])
@login_required
def mover_item(item_id):
    # 🔥 ESCUDO PROTECTOR: Agregamos al supervisor a la lista de permitidos
    roles_permitidos = ['admin', 'jefe_logistica', 'jefe_posventa', 'supervisor', 'operario_logistica', 'operario', 'encargado', 'jefe_materias_primas']
    
    if current_user.rol not in roles_permitidos:
        flash("🚫 Acceso denegado: Tu rol no tiene permisos para reubicar mercadería.", "error")
        return redirect(request.referrer)
    
    item_origen = Item.query.get_or_404(item_id)
    
    # 1. 🛡️ PROTECCIÓN DE DATOS
    try:
        cantidad_mover = int(request.form.get('cantidad_mover', 0))
        nivel_dest = int(request.form.get('nivel_destino', 1))
        pos_dest = int(request.form.get('posicion_destino', 1))
    except ValueError:
        flash("❌ Las coordenadas y la cantidad deben ser números válidos.", "error")
        return redirect(request.referrer)

    nombre_rack_destino = request.form.get('ubicacion_destino').strip()
    sub_destino_solicitada = request.form.get('sub_destino', 'General').strip()

    if cantidad_mover <= 0 or cantidad_mover > item_origen.cantidad:
        flash("❌ Cantidad inválida.", "error")
        return redirect(request.referrer)

    # =========================================================================
    # 2. 🔍 NUEVO BUSCADOR INTELIGENTE (RESUELVE RACKS DUPLICADOS)
    # =========================================================================
    ubi_destino = None

    # a) Si es una zona especial a piso (Recepción, Zona Blanca, etc)
    rack_dest_zona = Rack.query.filter_by(nombre=nombre_rack_destino).order_by(Rack.id.desc()).first()
    if rack_dest_zona and ("ZONA" in rack_dest_zona.nombre.upper() or "RECEPCIÓN" in rack_dest_zona.nombre.upper()):
        ubi_destino = Ubicacion.query.filter_by(rack_id=rack_dest_zona.id).first()

    # b) Si es un estante normal, buscamos TODAS las coincidencias
    if not ubi_destino:
        posibles_ubis = Ubicacion.query.join(Rack).filter(
            Rack.nombre == nombre_rack_destino,
            Ubicacion.nivel == nivel_dest,
            Ubicacion.posicion == pos_dest
        ).all()

        if len(posibles_ubis) == 1:
            # Si solo encontró uno, es ese y listo
            ubi_destino = posibles_ubis[0]
            
        elif len(posibles_ubis) > 1:
            # 🔥 DESEMPATE MÁGICO 🔥
            # Si encontró dos "R73-1", miramos el código generado de cada uno
            for u in posibles_ubis:
                codigo_sin_id = u.codigo_unico.split('-ID')[0]
                if '-' in codigo_sin_id:
                    # De "R73-1-1-10" sacamos "R73-1"
                    prefijo_real = "-".join(codigo_sin_id.split('-')[:-2])
                else:
                    prefijo_real = codigo_sin_id
                    
                # Si el prefijo coincide exactamente con el nombre que buscamos, ¡es este!
                if nombre_rack_destino == prefijo_real:
                    ubi_destino = u
                    break
            
            # Si por algún motivo extremo siguen empatados, agarra el último que creaste
            if not ubi_destino:
                ubi_destino = posibles_ubis[-1]

    if not ubi_destino:
        flash(f"❌ Error: La ubicación destino '{nombre_rack_destino}' N{nivel_dest} P{pos_dest} no existe.", "error")
        return redirect(request.referrer)

    rack_dest = ubi_destino.rack

    # =========================================================================
    # 🔥 EL CORAZÓN DE LA CORRECCIÓN: DECIDIR LA CAJA DESTINO
    # =========================================================================
    if sub_destino_solicitada != 'General':
        sub_ubicacion_final = sub_destino_solicitada
    else:
        sub_ubicacion_final = 'General'

    obs_final = item_origen.observaciones
    producto_final_id = item_origen.producto_id 

    # Reglas de cruce de Posventa a Logística (Se mantiene igual)
    viene_de_posventa = item_origen.ubicacion.rack.sector == 'posventa'
    viene_de_recepcion = "RECEPCIÓN" in item_origen.ubicacion.rack.nombre.upper()

    if (viene_de_posventa or viene_de_recepcion) and rack_dest.sector == 'logistica':
        prod_logistica = Producto.query.filter_by(sku=item_origen.producto_detalle.sku, sector='logistica').first()
        if not prod_logistica:
            flash(f"🚫 El SKU '{item_origen.producto_detalle.sku}' no existe en Logística.", "error")
            return redirect(request.referrer)
        if "RECEPCIÓN" not in rack_dest.nombre.upper():
            if sub_destino_solicitada == 'General': sub_ubicacion_final = 'General'
            obs_final = 'Stock revisado (Ingreso Posventa)'
            producto_final_id = prod_logistica.id 

    # =========================================================================
    # 4. 📦 LÓGICA DE MOVER STOCK (Poka-Yoke de Fusión Definitivo)
    # =========================================================================
    item_existente = Item.query.filter_by(
        ubicacion_id=ubi_destino.id, 
        producto_id=producto_final_id, 
        estado_calidad=item_origen.estado_calidad, 
        sub_ubicacion=sub_ubicacion_final,
        lote=item_origen.lote,                           
        fecha_vencimiento=item_origen.fecha_vencimiento  
    ).first()

    fantasma_vacio = Item.query.filter_by(
        ubicacion_id=ubi_destino.id, sub_ubicacion=sub_ubicacion_final
    ).join(Producto).filter(Producto.sku == 'SUBDIVISION_VACIA').first()

    if item_existente:
        item_existente.cantidad += cantidad_mover
        if fantasma_vacio:
            db.session.delete(fantasma_vacio)
    elif fantasma_vacio:
        fantasma_vacio.producto_id = producto_final_id
        fantasma_vacio.cantidad = cantidad_mover
        fantasma_vacio.estado_calidad = item_origen.estado_calidad
        fantasma_vacio.observaciones = obs_final
        fantasma_vacio.lote = item_origen.lote
        fantasma_vacio.fecha_vencimiento = item_origen.fecha_vencimiento
    else:
        nuevo_item = Item(
            ubicacion_id=ubi_destino.id, 
            producto_id=producto_final_id, 
            cantidad=cantidad_mover, 
            estado_calidad=item_origen.estado_calidad,
            sub_ubicacion=sub_ubicacion_final, 
            observaciones=obs_final,
            lote=item_origen.lote,
            fecha_vencimiento=item_origen.fecha_vencimiento
        )
        db.session.add(nuevo_item)

    # 6. 📝 REGISTRO EN EL HISTORIAL (REUBICACIÓN EXACTA)
    origen_txt = f"{item_origen.ubicacion.codigo_unico.split('-ID')[0]} [Caja: {item_origen.sub_ubicacion}]" if item_origen.sub_ubicacion not in ['General', 'vacia', None] else item_origen.ubicacion.codigo_unico.split('-ID')[0]
    destino_txt = f"{ubi_destino.codigo_unico.split('-ID')[0]} [Caja: {sub_ubicacion_final}]" if sub_ubicacion_final not in ['General', 'vacia', None] else ubi_destino.codigo_unico.split('-ID')[0]
    
    texto_transporte = f"HACIA: {destino_txt}"
    if item_origen.ubicacion.rack.sector != rack_dest.sector:
        texto_transporte = f"CRUCE A {rack_dest.sector.upper()}: {destino_txt}"

    log_mov = Movimiento(
        tipo='movimiento',
        sku=item_origen.producto_detalle.sku,
        cantidad=cantidad_mover,
        origen=origen_txt,
        transporte=texto_transporte,
        usuario=current_user.username,
        sector=item_origen.ubicacion.rack.sector 
    )
    db.session.add(log_mov)

    if item_origen.ubicacion.rack.sector != rack_dest.sector:
        log_mov_dest = Movimiento(
            tipo='movimiento',
            sku=item_origen.producto_detalle.sku,
            cantidad=cantidad_mover,
            origen=origen_txt,
            transporte=texto_transporte,
            usuario=current_user.username,
            sector=rack_dest.sector
        )
        db.session.add(log_mov_dest)

    # 5. 📉 RESTAR DEL ORIGEN
    item_origen.cantidad -= cantidad_mover
    if item_origen.cantidad <= 0:
        
        # 🔥 FIX (ASPIRADORA DE REMITOS): Si es General, o está en Recepción, o la caja es un Remito, SE BORRA 100%
        es_recepcion = item_origen.ubicacion.rack.descripcion == '[ADN_RECEPCION]' or "RECEPCI" in item_origen.ubicacion.rack.nombre.upper()
        es_remito = str(item_origen.sub_ubicacion).startswith('R-')
        
        if item_origen.sub_ubicacion != 'General' and not es_recepcion and not es_remito:
            prod_vacio = Producto.query.filter_by(sku='SUBDIVISION_VACIA', sector=item_origen.ubicacion.rack.sector).first()
            if prod_vacio:
                item_origen.producto_id = prod_vacio.id
                item_origen.cantidad = 0
                item_origen.estado_calidad = 'vacia'
                item_origen.observaciones = 'Caja libre (vaciada por reubicación)'
                item_origen.lote = None
                item_origen.fecha_vencimiento = None
            else:
                db.session.delete(item_origen)
        else:
            # Si era un remito o estaba en recepción, se destruye la fila sin piedad
            db.session.delete(item_origen)

    db.session.commit()
    ejecutar_radar_interno()
    flash(f"📦 Movido correctamente a {nombre_rack_destino} (Caja: {sub_ubicacion_final})", "success")
    return redirect(request.referrer)

# 1. MUESTRA LA PANTALLA DEL SCANNER
@app.route('/escanear_destino/<int:item_id>')
@login_required
def escanear_destino(item_id):
    # Nota: Asegurate de usar el nombre correcto de tu modelo (Item o ItemUbicacion)
    item = Item.query.get_or_404(item_id) 
    
    # 1. Traemos todas las ubicaciones para validar el destino después
    todas_ubicaciones = Ubicacion.query.all()
    lista_codigos = [u.codigo_unico for u in todas_ubicaciones]
    
    # 2. Le pasamos el SKU del item actual para la primera validación
    sku_esperado = item.producto_detalle.sku
    
    return render_template('escanear_destino.html', 
                           item=item, 
                           codigos_validos=lista_codigos,
                           sku_esperado=sku_esperado)

# 2. PROCESA EL DISPARO DE LA PISTOLA (Basado en tu lógica original)
@app.route('/procesar_movimiento_scanner/<int:item_id>', methods=['POST'])
@login_required
def procesar_movimiento_scanner(item_id):
    item_origen = Item.query.get_or_404(item_id)
    
    # 1. 🛡️ PROTECCIÓN DE DATOS (Del Scanner)
    try:
        cantidad_mover = int(request.form.get('cantidad', 1))
    except ValueError:
        flash("❌ La cantidad debe ser un número válido.", "error")
        return redirect(url_for('escanear_destino', item_id=item_id))

    codigo_escaneado = request.form.get('codigo_destino', '').strip().upper()

    if cantidad_mover <= 0 or cantidad_mover > item_origen.cantidad:
        flash("❌ Cantidad inválida o supera el stock actual.", "error")
        return redirect(url_for('escanear_destino', item_id=item_id))

    
    
    # 2. 🔍 BUSCAMOS LA UBICACIÓN EXACTA (Con traductor de código de barras físico)
    ubi_destino = Ubicacion.query.filter_by(codigo_unico=codigo_escaneado).first()
    
    # 🔥 TRADUCTOR WMS: Si la pistola lee "Pasillo-Posición-Nivel" (Ej: 1-1-1000) 🔥
    if not ubi_destino and '-' in codigo_escaneado:
        partes = codigo_escaneado.split('-')
        if len(partes) >= 3:
            num_nivel = partes[-1]
            num_posicion = partes[-2]
            nombre_pasillo = "-".join(partes[:-2])
            
            posibles_ubis = Ubicacion.query.filter_by(posicion=num_posicion, nivel=num_nivel).all()
            for u in posibles_ubis:
                prefijo_real = u.codigo_unico.split('-ID')[0].rsplit('-', 2)[0]
                if nombre_pasillo == prefijo_real:
                    ubi_destino = u
                    break

    # Por si escriben a mano el nombre de una Zona a piso (Ej: "ZONA BLANCA")
    if not ubi_destino:
        rack_dest = Rack.query.filter_by(nombre=codigo_escaneado).first()
        if rack_dest and ("ZONA" in rack_dest.nombre.upper() or "RECEPCI" in rack_dest.nombre.upper()):
            ubi_destino = Ubicacion.query.filter_by(rack_id=rack_dest.id).first()

    if not ubi_destino:
        flash(f"❌ Código '{codigo_escaneado}' no reconocido en el sistema.", "error")
        return redirect(url_for('escanear_destino', item_id=item_id))

    rack_dest = ubi_destino.rack

    # =========================================================================
    # 🔥 LA REGLA DE ORO: EL LAVADERO DE REMITOS (Cruce Posventa -> Logística)
    # =========================================================================
    sub_ubicacion_final = item_origen.sub_ubicacion
    obs_final = item_origen.observaciones
    producto_final_id = item_origen.producto_id # Por defecto mantiene su ID original

    viene_de_posventa = item_origen.ubicacion.rack.sector == 'posventa'
    viene_de_recepcion = "RECEPCIÓN" in item_origen.ubicacion.rack.nombre.upper()

    # Si cruza la frontera hacia Logística...
    if (viene_de_posventa or viene_de_recepcion) and rack_dest.sector == 'logistica':
        
        # 🛡️ PUESTO DE ADUANA: Verificamos catálogo de Logística
        prod_logistica = Producto.query.filter_by(sku=item_origen.producto_detalle.sku, sector='logistica').first()
        
        if not prod_logistica:
            flash(f"🚫 No se puede mover: El SKU '{item_origen.producto_detalle.sku}' no existe en el catálogo de Logística.", "error")
            return redirect(url_for('escanear_destino', item_id=item_id))
        
        # Si casualmente no lo están moviendo a la bandeja de recepción... ¡Lo lavamos y nacionalizamos!
        if "RECEPCIÓN" not in rack_dest.nombre.upper():
            sub_ubicacion_final = 'General'
            obs_final = 'Stock revisado (Ingreso Posventa vía Scanner)'
            producto_final_id = prod_logistica.id # 🪄 MAGIA: Lo vinculamos al catálogo de Logística
            print(f"🔄 Ítem {item_origen.producto_detalle.sku} nacionalizado a Logística (Scanner)")

    # 4. 📦 LÓGICA DE MOVER STOCK (Unificación)
    item_existente = Item.query.filter_by(
        ubicacion_id=ubi_destino.id,
        producto_id=producto_final_id, 
        estado_calidad=item_origen.estado_calidad,
        sub_ubicacion=sub_ubicacion_final
    ).first()

    if item_existente:
        item_existente.cantidad += cantidad_mover
    else:
        nuevo_item = Item(
            ubicacion_id=ubi_destino.id,
            producto_id=producto_final_id, 
            cantidad=cantidad_mover,
            estado_calidad=item_origen.estado_calidad,
            sub_ubicacion=sub_ubicacion_final,
            observaciones=obs_final
        )
        db.session.add(nuevo_item)

    # 5. 📉 RESTAR DEL ORIGEN
    item_origen.cantidad -= cantidad_mover
    if item_origen.cantidad <= 0:
        db.session.delete(item_origen)

    # 6. 📝 REGISTRO EN EL HISTORIAL (CRUCE DE FRONTERA)
    if item_origen.ubicacion.rack.sector != rack_dest.sector:
        log_mov = Movimiento(
            tipo='movimiento',
            sku=item_origen.producto_detalle.sku,
            cantidad=cantidad_mover,
            origen=item_origen.ubicacion.codigo_unico.split('-ID')[0],
            transporte=f"CRUCE A {rack_dest.sector.upper()} ({rack_dest.nombre}) [SCANNER]",
            usuario=current_user.username,
            sector=rack_dest.sector  # Lo anotamos en el historial del sector destino
        )
        db.session.add(log_mov)

    db.session.commit()
    flash(f"📦 Movido correctamente a {ubi_destino.codigo_unico}", "success")
    return redirect(url_for('detalle_ubicacion', rack_id=item_origen.ubicacion.rack_id, nivel=item_origen.ubicacion.nivel, pos=item_origen.ubicacion.posicion))

@app.route('/despachar_item/<int:item_id>', methods=['POST'])
@login_required
def despachar_item(item_id):
    item = Item.query.get_or_404(item_id)
    cantidad_despacho = int(request.form.get('cantidad_despacho', 0))

    if cantidad_despacho <= 0 or cantidad_despacho > item.cantidad:
        flash("❌ Cantidad inválida para el despacho.", "error")
        return redirect(request.referrer)

    # 🛡️ EL PUESTO DE ADUANA: ¿Logística conoce este SKU?
    prod_logistica = Producto.query.filter_by(sku=item.producto_detalle.sku, sector='logistica').first()
    if not prod_logistica:
        flash(f"🚫 Bloqueado: No podés enviar el SKU '{item.producto_detalle.sku}' porque Logística no lo tiene dado de alta en su catálogo.", "error")
        return redirect(request.referrer)

    # Generamos un número de remito único basado en el ID o tiempo
    # En 2026, lo hacemos así: R-26-ID
    ultimo_id = Transferencia.query.order_by(Transferencia.id.desc()).first()
    next_id = (ultimo_id.id + 1) if ultimo_id else 1
    nro_remito = f"R-26-{next_id:04d}"

    # 1. Creamos la transferencia
    nueva_transf = Transferencia(
        remito_nro=nro_remito,
        sku=item.producto_detalle.sku,
        descripcion=item.producto_detalle.descripcion,
        cantidad=cantidad_despacho,
        estado_calidad=item.estado_calidad,
        usuario_envia=current_user.username
    )
    db.session.add(nueva_transf)

    # =================================================================
    # 🔥 2. NUEVO: REGISTRO EN EL HISTORIAL DE POSVENTA 🔥
    # =================================================================
    origen_txt = item.ubicacion.codigo_unico.split('-ID')[0]
    log_mov = Movimiento(
        tipo='movimiento',
        sku=item.producto_detalle.sku,
        cantidad=cantidad_despacho,
        origen=origen_txt,
        transporte=f"REMITO A LOGÍSTICA: {nro_remito}",
        usuario=current_user.username,
        sector=item.ubicacion.rack.sector # Sector Posventa
    )
    db.session.add(log_mov)

    # 3. Restamos de Posventa
    item.cantidad -= cantidad_despacho
    if item.cantidad <= 0:
        db.session.delete(item)
    
    db.session.commit()

    flash(f"📦 Remito {nro_remito} generado. ¡Listo para imprimir!", "success")
    return redirect(url_for('ver_remito', trans_id=nueva_transf.id))

# A. PANTALLA DE RECEPCIÓN PARA LOGÍSTICA
@app.route('/recepcion_logistica')
@login_required
def recepcion_logistica():
    if current_user.rol not in ['admin', 'jefe_logistica', 'stock', 'supervisor', 'consultas']:
        flash("⚠️ No tenés permisos.", "error")
        return redirect(url_for('index'))

    pendientes = Transferencia.query.filter_by(estado='En Camino').all()
    # 🔍 Agregamos esto para que el select tenga opciones:
    racks_logistica = Rack.query.filter_by(sector='logistica').all()
    
    return render_template('recepcion_logistica.html', pendientes=pendientes, racks=racks_logistica)

# B. ACCIÓN DE CONFIRMAR RECEPCIÓN
# B. ACCIÓN DE CONFIRMAR RECEPCIÓN
@app.route('/confirmar_recepcion/<int:trans_id>', methods=['POST'])
@login_required
def confirmar_recepcion(trans_id):
    t = Transferencia.query.get_or_404(trans_id)
    
    # 🔥 FIX: Atrapamos la cantidad real que ingresó el operario en el casillero
    try:
        cant_recibida = int(request.form.get('cantidad_recibida', t.cantidad))
    except:
        cant_recibida = t.cantidad

    if cant_recibida < 0 or cant_recibida > t.cantidad:
        flash("❌ Cantidad ingresada inválida.", "error")
        return redirect(request.referrer)

    # Calculamos si falta algo
    faltante = t.cantidad - cant_recibida

    # 1. 🔍 BÚSQUEDA POR ADN: Buscamos el rack que tenga nuestra etiqueta invisible
    rack_rec = Rack.query.filter(Rack.sector == 'logistica', Rack.descripcion == '[ADN_RECEPCION]').first()

    # 2. Si no lo encuentra por ADN, buscamos el nombre por defecto y lo marcamos para siempre
    if not rack_rec:
        rack_rec = Rack.query.filter_by(nombre="📥 RECEPCIÓN DESDE POSVENTA", sector="logistica").first()
        if rack_rec:
            rack_rec.descripcion = "[ADN_RECEPCION]"
            db.session.commit()

    # 3. Si el rack directamente no existe, creamos uno nuevo ya marcado
    if not rack_rec:
        rack_rec = Rack(
            nombre="📥 RECEPCIÓN", 
            sector="logistica", 
            niveles=1, 
            posiciones=1, 
            multi_nivel=1,
            descripcion="[ADN_RECEPCION]" # <-- El sello invisible
        )
        db.session.add(rack_rec)
        db.session.commit()
        db.session.refresh(rack_rec)

    # 4. Buscamos o creamos el "hueco" dentro de ese rack
    ubi_rec = Ubicacion.query.filter_by(rack_id=rack_rec.id).first()
    if not ubi_rec:
        ubi_rec = Ubicacion(
            rack_id=rack_rec.id, 
            posicion=1, 
            nivel=1, 
            codigo_unico=f"REC-PV-LOG-ID{rack_rec.id}",
            estado='Disponible'
        )
        db.session.add(ubi_rec)
        db.session.commit()
        db.session.refresh(ubi_rec)

    # 5. Procesamos el ingreso de la mercadería
    prod = Producto.query.filter_by(sku=t.sku, sector='logistica').first()
    if not prod:
        flash(f"❌ El SKU {t.sku} no existe en Logística.", "error")
        return redirect(request.referrer)

    # Detectar origen y limpiar el número de remito (por si es de fábrica)
    if t.remito_nro.count('-') >= 3:
        origen_real = "Fábrica"
        remito_limpio = t.remito_nro.rsplit('-', 1)[0] 
    else:
        origen_real = "Posventa"
        remito_limpio = t.remito_nro

    texto_observacion = f"Remito: {remito_limpio} (Desde {origen_real})"

    # 🔥 SI LLEGÓ MERCADERÍA (> 0), LA INGRESAMOS A LOGÍSTICA 🔥
    if cant_recibida > 0:
        item_exist = Item.query.filter_by(ubicacion_id=ubi_rec.id, producto_id=prod.id, sub_ubicacion=t.remito_nro).first()
        
        if item_exist:
            item_exist.cantidad += cant_recibida
            item_exist.observaciones = texto_observacion
        else:
            nuevo_item = Item(
                ubicacion_id=ubi_rec.id,
                producto_id=prod.id,
                cantidad=cant_recibida,
                estado_calidad=t.estado_calidad,
                sub_ubicacion=t.remito_nro,
                observaciones=texto_observacion
            )
            db.session.add(nuevo_item)

        # Registramos el ingreso exitoso en el historial
        log_rec = Movimiento(
            tipo='ingreso',
            sku=t.sku,
            cantidad=cant_recibida,
            origen="REMITO INTERNO",
            transporte=f"RECEPCIÓN: {remito_limpio}",
            usuario=current_user.username,
            sector='logistica'
        )
        db.session.add(log_rec)

    # 🔥 SI HUBO FALTANTE, LO ANOTAMOS EN EL HISTORIAL DE ANULADOS/INCOMPLETOS 🔥
    if faltante > 0:
        log_merma = Movimiento(
            tipo='anulacion', 
            sku=t.sku,
            cantidad=faltante,
            origen=f"FALTANTE REMITO {remito_limpio}",
            transporte=f"Diferencia de Recepción (Origen: {origen_real})",
            usuario=current_user.username,
            sector='logistica'
        )
        db.session.add(log_merma)

    # Actualizamos el estado del remito dependiendo de lo que pasó
    if cant_recibida == 0:
        t.estado = 'Rechazado (0 Recibidos)'
        flash(f"🚫 Rechazaste el total del remito {remito_limpio}.", "warning")
    elif faltante > 0:
        t.estado = 'Recibido Parcial'
        flash(f"⚠️ Recepción Parcial: Ingresaron {cant_recibida}. Se registró un faltante de {faltante} en el historial.", "info")
    else:
        t.estado = 'Recibido'
        flash(f"✅ Recibido exitosamente en Recepción", "success")

    db.session.commit()
    return redirect(url_for('recepcion_logistica'))


@app.route('/ver_remito/<int:trans_id>')
@login_required
def ver_remito(trans_id):
    # Buscamos la transferencia en la base de datos
    t = Transferencia.query.get_or_404(trans_id)
    # Mostramos el archivo remito.html que creamos antes
    return render_template('remito.html', t=t)

@app.route('/historial_remitos')
@login_required
def historial_remitos():
    
    # 🔥 CANDADO SEGURO (Lista Blanca): Solo la élite entra acá.
    roles_vip = ['admin', 'jefe_logistica', 'stock', 'supervisor', 'consultas']
    
    if current_user.rol not in roles_vip:
        flash("🚫 Acceso denegado: Tu perfil no está autorizado para ver los remitos.", "error")
        return redirect(url_for('logistica'))
        
    # Buscamos todos los remitos. 
    remitos = Transferencia.query.order_by(Transferencia.fecha_envio.desc()).all()
    return render_template('historial_remitos.html', remitos=remitos)

from sqlalchemy import nullslast

@app.route('/produccion')
@login_required
def produccion():
    roles_permitidos = ['admin', 'supervisor_produccion', 'supervisor_produccio', 'operario_produccion', 'produccion', 'jefe_produccion', 'planificacion', 'encargado']
    
    if current_user.rol not in roles_permitidos:
        flash('🚫 Acceso denegado al módulo de Producción.', 'error')
        return redirect(url_for('home'))
    
    hoy_obj = hora_argentina().date() 

    # 1. Traemos todas las órdenes individuales activas (Pendientes, En Proceso, Finalizados recientes)
    ordenes_activas = OrdenProduccion.query.filter(
        OrdenProduccion.estado.in_(['Pendiente', 'En Proceso', 'Finalizado'])
    ).order_by(
        OrdenProduccion.fecha_planificada.is_(None), 
        OrdenProduccion.fecha_planificada.asc(), 
        OrdenProduccion.prioridad.desc(), 
        OrdenProduccion.fecha_solicitud.asc()
    ).all()

    # ================================================================
    # 🔥 NUEVO MOTOR AGREGADOR DE TOTALES 🔥
    # ================================================================
    resumen_totales = {}
    
    for orden in ordenes_activas:
        # Solo sumamos lo que está 'Pendiente' o 'En Proceso', no los Finalizados
        if orden.estado in ['Pendiente', 'En Proceso']:
            sku = orden.sku
            if not sku: continue # Saltamos datos inválidos si existen

            # Si el SKU ya está en la lista, sumamos la cantidad
            if sku not in resumen_totales:
                resumen_totales[sku] = {
                    'descripcion': orden.descripcion or 'Sin descripción',
                    'cantidad_total': 0,
                    'es_urgente': False
                }
            
            resumen_totales[sku]['cantidad_total'] += orden.cantidad
            
            # Si al menos un pedido es urgente, marcamos todo el código como urgente en el resumen
            if orden.prioridad == 'URGENTE':
                resumen_totales[sku]['es_urgente'] = True

    # Ordenamos el resumen por SKU para que sea fácil de encontrar
    resumen_totales_ordenado = dict(sorted(resumen_totales.items()))

    # ================================================================
    
    # ... (Todo el resto de tu lógica del cronograma queda EXACTAMENTE IGUAL) ...
    config_alm = ConfiguracionProduccion.query.first()
    
    # MOTOR DEL CRONOGRAMA DE PRODUCCIÓN (Mantenlo tal cual lo tienes)
    dias_calendario = [hoy_obj + timedelta(days=i) for i in range(15)]
    dias_headers = [d.strftime('%d/%m') for d in dias_calendario]
    dias_keys = [d.strftime('%Y-%m-%d') for d in dias_calendario]

    ordenes_programadas = OrdenProduccion.query.filter(
        OrdenProduccion.fecha_planificada >= hoy_obj,
        OrdenProduccion.fecha_planificada <= dias_calendario[-1],
        OrdenProduccion.estado.in_(['Pendiente', 'En Proceso'])
    ).all()

    mapa_calendario = {}
    for ord_p in ordenes_programadas:
        sku = ord_p.sku
        f_key = ord_p.fecha_planificada.strftime('%Y-%m-%d') if ord_p.fecha_planificada else hoy_obj.strftime('%Y-%m-%d')
        if sku not in mapa_calendario:
            mapa_calendario[sku] = {'descripcion': ord_p.descripcion, 'fechas': {}}
        mapa_calendario[sku]['fechas'][f_key] = mapa_calendario[sku]['fechas'].get(f_key, 0) + ord_p.cantidad

    datos_calendario = []
    for sku, info in mapa_calendario.items():
        fila = {'sku': sku, 'descripcion': info['descripcion'], 'cantidades': []}
        for dk in dias_keys:
            fila['cantidades'].append(info['fechas'].get(dk, 0))
        datos_calendario.append(fila)
        
    datos_calendario.sort(key=lambda x: sum(x['cantidades']), reverse=True)

    return render_template('produccion.html', 
                           ordenes=ordenes_activas, 
                           resumen_totales=resumen_totales_ordenado, # 🔥 LE ENVIAMOS EL RESUMEN AL HTML
                           hoy=hoy_obj, 
                           config_alm=config_alm,
                           dias_headers=dias_headers, 
                           datos_calendario=datos_calendario)

@app.route('/produccion/cambiar_estado_admin/<int:id>', methods=['POST'])
@login_required
def cambiar_estado_produccion_admin(id):
    # 🔒 SUPER SEGURIDAD: Solo el rol 'admin' exacto puede entrar acá
    if current_user.rol != 'admin':
        flash("🚫 No tienes permisos de súper-usuario para forzar cambios de estado.", "error")
        return redirect(url_for('historial_produccion'))

    orden = OrdenProduccion.query.get_or_404(id)
    nuevo_estado = request.form.get('nuevo_estado')
    estado_anterior = orden.estado

    if nuevo_estado:
        orden.estado = nuevo_estado
        
        # Si lo pasamos a Finalizado o Entregado, nos aseguramos que tenga fecha de fin
        if nuevo_estado in ['Finalizado', 'Entregado'] and not orden.fecha_fin:
            orden.fecha_fin = hora_argentina()
            orden.operario_fin = current_user.username

        # Si lo volvemos a Pendiente o Proceso, limpiamos la fecha de fin para que vuelva al tablero activo
        if nuevo_estado in ['Pendiente', 'En Proceso']:
            orden.fecha_fin = None
            orden.operario_fin = None

        # Dejamos rastro en la descripción para auditoría
        registro_cambio = f" [Editado por Admin: {estado_anterior} -> {nuevo_estado}]"
        if registro_cambio not in (orden.descripcion or ""):
            orden.descripcion = (orden.descripcion or "") + registro_cambio

        db.session.commit()
        flash(f"🛠️ Estado de {orden.sku} corregido a {nuevo_estado}.", "success")

    return redirect(url_for('historial_produccion'))

@app.route('/produccion/imprimir_pedidos')
@login_required
def imprimir_pedidos_produccion():
    # Seguridad de acceso
    if current_user.rol not in ['admin', 'supervisor_produccion', 'supervisor_produccio', 'operario_produccion', 'produccion', 'jefe_produccion', 'planificacion', 'encargado']:
        flash('🚫 Acceso denegado.', 'error')
        return redirect(url_for('produccion'))

    # Traemos las órdenes activas ordenadas exactamente igual que en el tablero
    ordenes_activas = OrdenProduccion.query.filter(
        OrdenProduccion.estado.in_(['Pendiente', 'En Proceso'])
    ).order_by(
        OrdenProduccion.fecha_planificada.asc(), 
        OrdenProduccion.prioridad.desc(), 
        OrdenProduccion.fecha_solicitud.asc()
    ).all()

    hoy = hora_argentina()

    return render_template('imprimir_pedidos_produccion.html', ordenes=ordenes_activas, hoy=hoy)

# 🔥 NUEVA API DE BÚSQUEDA V8 PARA ETIQUETAS (Pegala abajo de produccion)

@app.route('/solicitar_produccion', methods=['POST'])
@login_required
def solicitar_produccion():
    if current_user.rol not in ['admin', 'jefe_logistica', 'supervisor']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(request.referrer)
    
    sku = request.form.get('sku', '').strip().upper()
    if not sku.startswith('CORT'):
        flash(f"🚫 Solo se fabrica la línea CORT.", "error")
        return redirect(request.referrer)

    cant_raw = request.form.get('cantidad', '').strip()
    try:
        cantidad = int(cant_raw) if cant_raw else 1
    except ValueError:
        cantidad = 1

    lote = request.form.get('lote')
    prioridad = request.form.get('prioridad', 'Normal') 
    ahora = hora_argentina()

    # 🔥 BANDERA DEL RELOJ GENERAL: Si este es el primer producto que tocan del lote, arranca.
    bandera = Movimiento.query.filter_by(sku='🚀 LOTE INICIADO', origen=f"Ruta: {lote}").first()
    if not bandera:
        nuevo_inicio = Movimiento(
            tipo='info', sku='🚀 LOTE INICIADO', cantidad=0,
            origen=f"Ruta: {lote}", transporte=ahora.strftime('%Y-%m-%d %H:%M:%S'),
            usuario=current_user.username, sector='logistica'
        )
        db.session.add(nuevo_inicio)

    # 🔥 FIX 3: RELOJ INDIVIDUAL. Le ponemos hora_inicio SÓLO a esta tarea que pedimos a fábrica.
    # No pisamos las de los demás productos del lote.
    tarea_especifica = TareaPicking.query.filter_by(sku=sku, zona=lote).first()
    if tarea_especifica and not tarea_especifica.hora_inicio:
        tarea_especifica.hora_inicio = ahora

    producto_db = Producto.query.filter_by(sku=sku, sector='logistica').first()
    descripcion_real = producto_db.descripcion if producto_db else "Sin descripción"

    nueva_orden = OrdenProduccion(
        sku=sku, 
        cantidad=cantidad, 
        lote_referencia=lote, 
        descripcion=descripcion_real,
        prioridad=prioridad
    )
    db.session.add(nueva_orden)
    db.session.commit()
    
    flash(f"🛠️ Solicitud enviada a Fábrica (Prioridad: {prioridad}).", "success")
    return redirect(request.referrer)

@app.route('/produccion/crear_planificada', methods=['POST'])
@login_required
def crear_produccion_planificada():
    roles_autorizados = ['admin', 'supervisor_produccion', 'supervisor_produccio', 'jefe_produccion', 'planificacion', 'encargado']
    if current_user.rol not in roles_autorizados:
        flash("🚫 Acceso denegado. No tienes permisos para planificar producción.", "error")
        return redirect(request.referrer)

    sku = request.form.get('sku', '').strip().upper()
    cantidad = int(request.form.get('cantidad', 1))
    referencia = request.form.get('referencia', 'Planificación Interna').strip()
    
    # 🔥 CAPTURA DE FECHA IDÉNTICA A VENTAS
    fecha_str = request.form.get('fecha_planificada')
    fecha_plan = None
    if fecha_str:
        try:
            # Lo guardamos como datetime completo para que no falle la comparación en Jinja
            fecha_plan = datetime.strptime(fecha_str, '%Y-%m-%d')
        except:
            fecha_plan = datetime.now()
    else:
        fecha_plan = datetime.now()

    # Buscamos la descripción oficial
    producto_db = Producto.query.filter_by(sku=sku, sector='logistica').first()
    descripcion_real = producto_db.descripcion if producto_db else "Sin descripción"

    # Creamos la orden
    nueva_orden = OrdenProduccion(
        sku=sku,
        cantidad=cantidad,
        lote_referencia=referencia,
        descripcion=descripcion_real,
        origen_pedido='Planificación',
        fecha_planificada=fecha_plan
    )
    
    db.session.add(nueva_orden)
    db.session.commit()
    
    flash(f"✅ Orden planificada de {cantidad}x {sku} agregada a la cola.", "success")
    return redirect(request.referrer)

@app.route('/anular_orden_produccion/<int:orden_id>', methods=['POST'])
@login_required
def anular_orden_produccion(orden_id):
    # Verificamos permisos
    if current_user.rol not in ['admin', 'jefe_logistica', 'ruteador', 'Ruteador', 'operario']:
        flash("⚠️ No tienes permisos para anular pedidos a producción.", "error")
        return redirect(request.referrer)

    orden = OrdenProduccion.query.get_or_404(orden_id)

    # 🛡️ POKA-YOKE: Si Producción ya empezó a fabricarlo, no dejamos que lo borren sin avisar.
    if orden.estado == 'En Proceso':
        flash(f"⚠️ Producción ya empezó a fabricar el SKU {orden.sku}. Hablá con ellos para frenarlo.", "error")
        return redirect(request.referrer)

    # Si sigue en 'Pendiente', lo borramos como si nada hubiera pasado
    db.session.delete(orden)
    db.session.commit()

    flash(f"🗑️ Solicitud de producción para el SKU {orden.sku} cancelada.", "info")
    return redirect(request.referrer)

@app.route('/iniciar_fabricacion/<int:orden_id>', methods=['POST'])
@login_required
def iniciar_fabricacion(orden_id):
    # 🔥 FIX: Agregamos el control de seguridad para que nadie de otro sector toque esto
    if current_user.rol not in ['admin', 'supervisor_produccion', 'supervisor_produccio', 'operario_produccion', 'produccion', 'jefe_produccion', 'planificacion']:
        flash('🚫 Acceso denegado. No tienes permisos para iniciar órdenes.', 'error')
        return redirect(url_for('produccion'))
    
    

    orden = OrdenProduccion.query.get_or_404(orden_id)
    orden.estado = 'En Proceso'
    orden.fecha_inicio = hora_argentina()
    orden.operario_inicio = current_user.username
    db.session.commit()
    flash(f"🚀 Fabricación iniciada para {orden.sku}", "success")
    return redirect(url_for('produccion'))

@app.route('/finalizar_fabricacion/<int:orden_id>', methods=['POST'])
@login_required
def finalizar_fabricacion(orden_id):
    if current_user.rol not in ['admin', 'supervisor_produccion', 'supervisor_produccio', 'operario_produccion', 'produccion', 'jefe_produccion', 'planificacion']:
        flash('🚫 Acceso denegado.', 'error')
        return redirect(url_for('produccion'))

    orden = OrdenProduccion.query.get_or_404(orden_id)
    cantidad_real_str = request.form.get('cantidad_real')
    observacion_merma = request.form.get('observacion_merma', '').strip()
    
    if cantidad_real_str:
        try:
            cantidad_real = int(cantidad_real_str)
            if cantidad_real > 0 and cantidad_real != orden.cantidad:
                texto_extra = f" (Planificado: {orden.cantidad}u | Fabricado: {cantidad_real}u)"
                if observacion_merma: texto_extra += f" - Motivo merma: {observacion_merma}"
                orden.descripcion = f"{orden.descripcion}{texto_extra}"
                orden.cantidad = cantidad_real
        except: pass 

    # 🔥 VINCULACIÓN: Si viene de Ventas, marcamos como Finalizado
    if orden.origen_pedido == 'Ventas' and orden.lote_referencia.startswith('PED-'):
        try:
            pedido_id = int(orden.lote_referencia.replace('PED-', ''))
            pedido = PedidoCliente.query.get(pedido_id)
            if pedido:
                pedido.estado = 'Finalizado'
        except: pass

    orden.estado = 'Finalizado'
    orden.fecha_fin = hora_argentina()
    orden.operario_fin = current_user.username
    db.session.commit()
    flash(f"✅ Fabricación finalizada.", "success")
    return redirect(url_for('produccion'))


@app.route('/despachar_produccion/<int:tarea_id>/<int:orden_id>', methods=['POST'])
@login_required
def despachar_produccion(tarea_id, orden_id):
    tarea = TareaPicking.query.get_or_404(tarea_id)
    orden = OrdenProduccion.query.get_or_404(orden_id)
    zona_lote = tarea.zona
    ahora = hora_argentina()

    # Cálculo tiempo del SKU
    tiempo_formateado = ""
    if tarea.hora_inicio:
        diff = int((ahora.replace(tzinfo=None) - tarea.hora_inicio.replace(tzinfo=None)).total_seconds())
        tiempo_formateado = f" (⏱️ {diff // 60}m {diff % 60}s)"

    nuevo_log = Movimiento(tipo='despacho', sku=tarea.sku, cantidad=orden.cantidad, origen="FÁBRICA (Directo)", transporte=f"Ruta: {zona_lote}{tiempo_formateado}", usuario=current_user.username, sector="logistica")
    db.session.add(nuevo_log)

    if (tarea.cantidad - orden.cantidad) > 0:
        tarea.cantidad -= orden.cantidad
    else:
        db.session.delete(tarea)
    
    # 🔥 RESET "VUELTA DE CARRERA": La siguiente tarea empieza a contar desde ahora
    tareas_restantes = TareaPicking.query.filter_by(zona=zona_lote).all()
    for tr in tareas_restantes:
        tr.hora_inicio = ahora

    # Si es el final de la ruta
    db.session.flush()
    if TareaPicking.query.filter_by(zona=zona_lote).count() == 0:
        # BUSCAMOS EL PRIMER INICIO (ASC)
        mov_inicio = Movimiento.query.filter_by(sku='🚀 LOTE INICIADO', origen=f"Ruta: {zona_lote}").order_by(Movimiento.id.asc()).first()
        inicio_lote = datetime.strptime(mov_inicio.transporte, '%Y-%m-%d %H:%M:%S') if mov_inicio else ahora
        
        duracion = int((ahora.replace(tzinfo=None) - inicio_lote.replace(tzinfo=None)).total_seconds())
        bandera_fin = Movimiento(tipo='despacho', sku='🏁 LOTE FINALIZADO', cantidad=0, origen=f"Ruta: {zona_lote}", transporte=f"⏱️ {duracion // 60}m {duracion % 60}s (Inicio: {inicio_lote.strftime('%H:%M')} | Fin: {ahora.strftime('%H:%M')})", usuario="Equipo / " + current_user.username, sector='logistica')
        db.session.add(bandera_fin)

    orden.estado = 'Entregado'
    db.session.commit()
    return redirect(request.referrer)

@app.context_processor
def inyectar_notificaciones():
    # Solo calculamos si hay un usuario logueado
    if current_user.is_authenticated:
        
        # 1. Notificación para Logística: Remitos que vienen de Posventa ('En Camino')
        remitos_pendientes = Transferencia.query.filter_by(estado='En Camino').count()
        
        # 2. Notificación para Posventa: Tareas en la bandeja de entrada
        reparaciones_pendientes = Reparacion.query.filter_by(estado='Pendiente', sector='posventa').count()
        
        # 3. Notificación para Picking/Ruteadores: Hojas de ruta sin terminar
        picking_pendientes = TareaPicking.query.filter_by(estado='Pendiente').count()

        # 🔥 4. NUEVO: Notificación para Clarckistas (Reposiciones Automáticas o Manuales)
        repos_pendientes = TareaReposicion.query.filter_by(estado='Pendiente').count()

        return dict(
            notif_logistica=remitos_pendientes,
            notif_posventa=reparaciones_pendientes,
            notif_picking=picking_pendientes,
            notif_reposiciones=repos_pendientes # La pasamos al HTML
        )
    
    # Si no está logueado, devolvemos 0
    return dict(notif_logistica=0, notif_posventa=0, notif_picking=0, notif_reposiciones=0)


@app.route('/descargar_plantilla_pedidos')
@login_required
def descargar_plantilla_pedidos():
    # 1. Definimos las columnas estrictamente necesarias
    columnas = ['Fecha', 'Despacho', 'SKU', 'Cantidad']
    
    # 2. Fila de ejemplo clara para que el operario entienda qué poner
    ejemplo = [{
        'Fecha': datetime.now().strftime('%d/%m/%Y'),
        'Despacho': 'Andreani - Pedido 1234',
        'SKU': 'BOMBA-001',
        'Cantidad': 5
    }]
    
    df = pd.DataFrame(ejemplo, columns=columnas)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Plantilla Pedidos')
        
        # Ajustar ancho de columnas para que el ejemplo se lea bien
        worksheet = writer.sheets['Plantilla Pedidos']
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(cell.value)
                except:
                    pass
            worksheet.column_dimensions[column].width = (max_length + 5)

    output.seek(0)
    
    return send_file(
        output, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
        as_attachment=True, 
        download_name='Plantilla_Hojas_de_Ruta.xlsx'
    )

import io
import pandas as pd
from flask import send_file

@app.route('/exportar_movimientos/<sector>')
@login_required
def exportar_movimientos(sector):
    if current_user.rol not in ['admin', 'jefe_logistica', 'jefe_posventa']:
        flash("⚠️ No tienes permisos para exportar el historial.", "error")
        return redirect(request.referrer)

    if sector not in ['logistica', 'posventa']:
        sector = 'logistica'

    output = io.BytesIO()
    
    # Abrimos el creador de Excel
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        
        # ==========================================
        # 1. PESTAÑA: AJUSTES DE STOCK (Ambos sectores)
        # ==========================================
        ajustes = HistorialAjuste.query.filter_by(sector=sector).order_by(HistorialAjuste.fecha.desc()).all()
        data_aj = []
        for a in ajustes:
            diferencia = a.cantidad_nueva - a.cantidad_anterior
            signo = "+" if diferencia > 0 else ""
            data_aj.append({
                'Fecha': a.fecha.strftime('%d/%m/%Y %H:%M') if a.fecha else "S/D",
                'SKU': a.sku,
                'Descripción': a.descripcion,
                'Ubicación': a.ubicacion,
                'Cant. Anterior': a.cantidad_anterior,
                'Cant. Nueva': a.cantidad_nueva,
                'Diferencia': f"{signo}{diferencia}",
                'Motivo': a.motivo,
                'Operario': a.usuario
            })
        df_aj = pd.DataFrame(data_aj) if data_aj else pd.DataFrame([{'Info': 'No hay ajustes registrados'}])
        df_aj.to_excel(writer, index=False, sheet_name='Ajustes de Stock')

        # ==========================================
        # LÓGICA EXCLUSIVA PARA LOGÍSTICA
        # ==========================================
        if sector == 'logistica':
            
            # PESTAÑA: INGRESOS
            ingresos = Movimiento.query.filter_by(tipo='ingreso', sector=sector).order_by(Movimiento.fecha.desc()).all()
            data_in = []
            for i in ingresos:
                data_in.append({
                    'Fecha': i.fecha.strftime('%d/%m/%Y %H:%M') if i.fecha else "S/D",
                    'SKU': i.sku,
                    'Cant.': f"+{i.cantidad}",
                    'Referencia / Ubic.': i.origen.split('-ID')[0] if i.origen else 'S/D',
                    'Tipo de Carga': i.transporte if i.transporte else 'Carga Masiva',
                    'Operario': i.usuario
                })
            df_in = pd.DataFrame(data_in) if data_in else pd.DataFrame([{'Info': 'No hay ingresos registrados'}])
            df_in.to_excel(writer, index=False, sheet_name='Ingresos')

            # PESTAÑA: DESPACHOS
            despachos = Movimiento.query.filter_by(tipo='despacho', sector=sector).order_by(Movimiento.fecha.desc()).all()
            data_des = []
            for d in despachos:
                data_des.append({
                    'Fecha': d.fecha.strftime('%d/%m/%Y %H:%M') if d.fecha else "S/D",
                    'SKU': d.sku,
                    'Cant.': f"-{d.cantidad}",
                    'Origen': d.origen.split('-ID')[0] if d.origen else 'S/D',
                    'Orden / Transporte': d.transporte if d.transporte else 'Manual',
                    'Operario': d.usuario
                })
            df_des = pd.DataFrame(data_des) if data_des else pd.DataFrame([{'Info': 'No hay despachos registrados'}])
            df_des.to_excel(writer, index=False, sheet_name='Despachos')

            # PESTAÑA: ANULADOS (Ocultamos los cierres forzados)
            anulados = Movimiento.query.filter(
                Movimiento.tipo == 'anulacion', 
                Movimiento.sector == sector, 
                Movimiento.transporte != 'Cierre Forzado Incompleto',
                Movimiento.sku != '⚠️ CIERRE FORZADO'
            ).order_by(Movimiento.fecha.desc()).all()
            
            data_anu = []
            for a in anulados:
                data_anu.append({
                    'Fecha': a.fecha.strftime('%d/%m/%Y %H:%M') if a.fecha else "S/D",
                    'SKU': a.sku,
                    'Cant.': a.cantidad,
                    'Referencia Lote': a.origen if a.origen else 'S/D',
                    'Operario': a.usuario
                })
            df_anu = pd.DataFrame(data_anu) if data_anu else pd.DataFrame([{'Info': 'No hay anulaciones registradas'}])
            df_anu.to_excel(writer, index=False, sheet_name='Anulados')

            # PESTAÑA: PEDIDOS INCOMPLETOS
            incompletos = Movimiento.query.filter(
                Movimiento.tipo == 'anulacion', 
                Movimiento.sector == sector, 
                db.or_(
                    Movimiento.transporte == 'Cierre Forzado Incompleto', 
                    Movimiento.sku == '⚠️ CIERRE FORZADO'
                )
            ).order_by(Movimiento.fecha.desc()).all()
            
            data_inc = []
            for inc in incompletos:
                data_inc.append({
                    'Fecha': inc.fecha.strftime('%d/%m/%Y %H:%M') if inc.fecha else "S/D",
                    'Ruta / Lote': inc.origen,
                    'SKU Faltante': inc.sku,
                    'Cant. Faltante': f"-{inc.cantidad}" if inc.cantidad > 0 else "-",
                    'Cierre Autorizado Por / Motivo': inc.usuario if inc.transporte == 'Cierre Forzado Incompleto' else inc.transporte
                })
            df_inc = pd.DataFrame(data_inc) if data_inc else pd.DataFrame([{'Info': 'No hay pedidos incompletos'}])
            df_inc.to_excel(writer, index=False, sheet_name='Incompletos')

            # 🔥 NUEVA PESTAÑA: MOVIMIENTOS INTERNOS (LOGÍSTICA) 🔥
            movs_log = Movimiento.query.filter_by(tipo='movimiento', sector=sector).order_by(Movimiento.fecha.desc()).all()
            data_movs_log = []
            for m in movs_log:
                data_movs_log.append({
                    'Fecha': m.fecha.strftime('%d/%m/%Y %H:%M') if m.fecha else "S/D",
                    'SKU': m.sku,
                    'Cant.': m.cantidad,
                    'Origen': m.origen.split('-ID')[0] if m.origen else 'S/D',
                    'Destino / Transporte': m.transporte.split('-ID')[0] if m.transporte else 'S/D',
                    'Operario': m.usuario
                })
            df_movs_log = pd.DataFrame(data_movs_log) if data_movs_log else pd.DataFrame([{'Info': 'No hay movimientos internos'}])
            df_movs_log.to_excel(writer, index=False, sheet_name='Mov. Internos')

            # 🔥 NUEVA PESTAÑA: PEDIDOS A FABRICA 🔥
            # Ahora la base de datos lee de la tabla "OrdenProduccion" como en tu sistema original
            pedidos_fab = OrdenProduccion.query.filter_by(origen_pedido='Logística').order_by(OrdenProduccion.fecha_solicitud.desc()).all()
            data_ped = []
            for p in pedidos_fab:
                data_ped.append({
                    'Fecha Solicitud': p.fecha_solicitud.strftime('%d/%m/%Y %H:%M') if p.fecha_solicitud else "S/D",
                    'Ruta / Lote Origen': p.lote_referencia if p.lote_referencia else 'S/D',
                    'SKU': p.sku,
                    'Descripción': p.descripcion,
                    'Cantidad': p.cantidad,
                    'Prioridad': p.prioridad,
                    'Estado Fábrica': p.estado
                })
            df_ped = pd.DataFrame(data_ped) if data_ped else pd.DataFrame([{'Info': 'No hay pedidos a fábrica registrados'}])
            df_ped.to_excel(writer, index=False, sheet_name='Pedidos a Fabrica')

        # ==========================================
        # LÓGICA EXCLUSIVA PARA POSVENTA
        # ==========================================
        else:
            # PESTAÑA: MOVIMIENTOS INTERNOS
            movs = Movimiento.query.filter_by(tipo='movimiento', sector=sector).order_by(Movimiento.fecha.desc()).all()
            data_movs = []
            for m in movs:
                data_movs.append({
                    'Fecha': m.fecha.strftime('%d/%m/%Y %H:%M') if m.fecha else "S/D",
                    'SKU': m.sku,
                    'Cant.': m.cantidad,
                    'Origen': m.origen.replace('PV-', '').split('-ID')[0] if m.origen else 'S/D',
                    'Destino / Transporte': m.transporte.replace('PV-', '').split('-ID')[0] if m.transporte else 'S/D',
                    'Operario': m.usuario
                })
            df_movs = pd.DataFrame(data_movs) if data_movs else pd.DataFrame([{'Info': 'No hay movimientos internos'}])
            df_movs.to_excel(writer, index=False, sheet_name='Movimientos Internos')

        # ==========================================
        # DARLE FORMATO LINDO A TODAS LAS PESTAÑAS
        # ==========================================
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for col in worksheet.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(cell.value)
                    except:
                        pass
                worksheet.column_dimensions[column].width = max_length + 2

    output.seek(0)
    
    # Nombre del archivo dinámico
    fecha_hoy = hora_argentina().strftime("%d-%m-%Y")
    nombre_archivo = f'Historial_{sector.upper()}_{fecha_hoy}.xlsx'
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=nombre_archivo
    )

@app.route('/api/escanear_ubicacion/<path:codigo>')
@login_required
def escanear_ubicacion(codigo):
    codigo = codigo.strip().upper()
    ubi = None
    sub_detectada = 'General'

    # 🔥 NUEVO: Atajo mágico para la zona de Recepción
    if codigo in ['RECEPCION', 'RECEPCIÓN', 'REC-LOG', 'REC-PV-LOG']:
        rack_rec = Rack.query.filter(Rack.descripcion == '[ADN_RECEPCION]').first()
        if rack_rec and rack_rec.ubicaciones:
            ubi = rack_rec.ubicaciones[0]

    # 1. Intento por ID exacto
    if not ubi:
        ubi = Ubicacion.query.filter((Ubicacion.codigo_unico == codigo) | (Ubicacion.codigo_unico.like(f"{codigo}-ID%"))).first()
    
    # 2. Traductor Inteligente con Debug
    if not ubi and '-' in codigo:
        partes = codigo.split('-')
        if len(partes) >= 3:
            num_nivel = partes[-1]
            num_posicion = partes[-2]
            nombre_escaneado = "-".join(partes[:-2])
            
            print(f"DEBUG SCANNER: Buscando Pasillo '{nombre_escaneado}', Pos '{num_posicion}', Nivel '{num_nivel}'")
            
            posibles = Ubicacion.query.filter_by(posicion=num_posicion, nivel=num_nivel).all()
            for u in posibles:
                # Limpiamos el código de la DB para comparar (le sacamos el -ID)
                codigo_limpio = u.codigo_unico.split('-ID')[0]
                partes_db = codigo_limpio.split('-')
                prefijo_db = "-".join(partes_db[:-2])
                
                if nombre_escaneado == prefijo_db:
                    ubi = u
                    print(f"✅ Coincidencia encontrada: {u.codigo_unico}")
                    break

    if not ubi:
        # 3. ¿Es una subdivisión?
        item_sub = Item.query.filter_by(sub_ubicacion=codigo).first()
        if item_sub:
            ubi = item_sub.ubicacion
            sub_detectada = item_sub.sub_ubicacion

    if not ubi:
        print(f"❌ ERROR: No se encontró nada para '{codigo}'")
        return jsonify({'status': 'error', 'msg': f'❌ Ubicación "{codigo}" no encontrada.'})
        
    items = Item.query.filter(Item.ubicacion_id == ubi.id, Item.cantidad > 0).all()
    
    # 🔥 EL FIX ESTÁ ACÁ: Ponemos la descripción AL LADO del SKU, y devolvemos el "Sub:" a su lugar original
    lista_items = []
    for i in items:
        desc_corta = i.producto_detalle.descripcion[:25] # Cortamos a 25 letras para que no desborde la pantalla
        
        # Formato exacto que espera el Javascript del Scanner (3 partes separadas por "|"):
        texto_js = f"{i.producto_detalle.sku} ({desc_corta}) | Cant: {i.cantidad} | Sub: {i.sub_ubicacion}"
        
        lista_items.append({
            'id': i.id, 
            'texto': texto_js, 
            'sku': i.producto_detalle.sku, 
            'max': i.cantidad
        })
    
    return jsonify({
        'status': 'ok',
        'rack': ubi.rack.nombre,
        'nivel': ubi.nivel,
        'posicion': ubi.posicion,
        'sub_ubicacion': sub_detectada,
        'items': lista_items
    })


@app.route('/forzar_cierre_lote', methods=['POST'])
@login_required
def forzar_cierre_lote():
    # 1. Seguridad: Solo jefes pueden hacer esto
    if current_user.rol not in ['admin', 'jefe_logistica', 'supervisor']:
        flash("No tienes permisos para forzar el cierre de una carga.", "error")
        return redirect(request.referrer)

    lote_id = request.form.get('lote_id')
    # Rescatamos la observación del formulario
    observacion = request.form.get('observacion', '').strip()
    
    # 3. Validación estricta: Si no hay texto, lo rebotamos
    if not observacion:
        flash("⚠️ Es obligatorio ingresar un motivo para forzar el cierre.", "error")
        return redirect(request.referrer)

    tareas = TareaPicking.query.filter_by(zona=lote_id).all()
    
    if not tareas:
        return redirect(request.referrer)

    ahora = hora_argentina()
    
    # 4. Rescatamos la bandera ORIGINAL (la más antigua) para el reloj
    mov_inicio = Movimiento.query.filter_by(sku='🚀 LOTE INICIADO', origen=f"Lote: {lote_id}").order_by(Movimiento.id.asc()).first()
    
    if mov_inicio:
        try:
            inicio_lote_real = datetime.strptime(mov_inicio.transporte, '%Y-%m-%d %H:%M:%S')
        except:
            inicio_lote_real = tareas[0].hora_inicio if tareas[0].hora_inicio else ahora
    else:
        inicio_lote_real = tareas[0].hora_inicio if tareas[0].hora_inicio else ahora

    # 🔥 FIX: Usamos "inicio_lote_real" en vez de la variable "tarea" que no existía
    duracion_segundos = int((ahora.replace(tzinfo=None) - inicio_lote_real.replace(tzinfo=None)).total_seconds())
    
    min_totales = duracion_segundos // 60
    seg_totales = duracion_segundos % 60
    tiempo_total_str = f"{min_totales}m {seg_totales}s" if min_totales > 0 else f"{seg_totales}s"
    
    inicio_str = inicio_lote_real.strftime('%H:%M')
    fin_str = ahora.strftime('%H:%M')

    # 5. Guardamos en el historial la bandera general con el motivo y tiempo
    texto_historial = f"Motivo: {observacion} | ⏱️ {tiempo_total_str} (Inició: {inicio_str} | Cerró: {fin_str})"

    log_tiempo = Movimiento(
        tipo='anulacion', 
        sku='⚠️ CIERRE FORZADO',
        cantidad=0,
        origen=f"Lote: {lote_id}",
        transporte=texto_historial,
        usuario=current_user.username,
        sector='logistica'
    )
    db.session.add(log_tiempo)

    # 6. Registramos CADA SKU que quedó pendiente antes de borrarlo
    for t in tareas:
        log_incompleto = Movimiento(
            tipo='anulacion',
            sku=t.sku,
            cantidad=t.cantidad, # Anotamos la cantidad exacta que no se llegó a preparar
            origen=f"Lote: {lote_id}",
            transporte='Cierre Forzado Incompleto', # ¡La frase exacta que necesita el Historial!
            usuario=current_user.username,
            sector='logistica'
        )
        db.session.add(log_incompleto)
        
        # Ahora sí, borramos la tarea pendiente
        db.session.delete(t)
        
    db.session.commit()
    flash(f"⚠️ {lote_id} cerrada. Los ítems incompletos se enviaron al historial.", "info")
    ejecutar_radar_interno()
    return redirect(url_for('ver_picking'))

@app.route('/agarrar_tarea/<int:tarea_id>')
@login_required
def agarrar_tarea(tarea_id):
    tarea = TareaPicking.query.get_or_404(tarea_id)
    ahora = hora_argentina()

    # Si ya tiene picker y no soy yo, rebota
    if tarea.picker and tarea.picker != current_user.username:
        flash(f"⚠️ {tarea.picker} ya está preparando este SKU.", "error")
        return redirect(url_for('picking_detalle', lote=tarea.zona))

    # 🔥 BANDERA DE INICIO: Buscamos si ya existe el inicio de esta ruta
    bandera = Movimiento.query.filter_by(sku='🚀 LOTE INICIADO', origen=f"Ruta: {tarea.zona}").first()
    if not bandera:
        nuevo_inicio = Movimiento(
            tipo='info', sku='🚀 LOTE INICIADO', cantidad=0,
            origen=f"Ruta: {tarea.zona}",
            transporte=ahora.strftime('%Y-%m-%d %H:%M:%S'), # Guardamos el "Nacimiento" del lote
            usuario=current_user.username, sector='logistica'
        )
        db.session.add(nuevo_inicio)
        # Seteamos el inicio de TODOS los SKUs de esta ruta ahora mismo
        tareas_lote = TareaPicking.query.filter_by(zona=tarea.zona).all()
        for t in tareas_lote:
            t.hora_inicio = ahora

    tarea.picker = current_user.username
    db.session.commit()
    return redirect(url_for('despachos', sku=tarea.sku, cantidad_hoja=tarea.cantidad, lote_nombre=tarea.zona))


@app.route('/toggle_bloqueo_ubicacion/<int:ubicacion_id>', methods=['POST'])
@login_required
def toggle_bloqueo_ubicacion(ubicacion_id):
    if current_user.rol not in ['admin', 'supervisor', 'jefe_logistica']:
        flash("No tienes permisos para bloquear posiciones.", "error")
        return redirect(request.referrer)

    ubi = Ubicacion.query.get_or_404(ubicacion_id)
    
    # Prevenimos que bloqueen una posición que tiene cosas adentro
    tiene_stock = Item.query.filter_by(ubicacion_id=ubi.id).first()
    if tiene_stock and ubi.estado != 'Bloqueada':
        flash("No puedes bloquear una posición que tiene mercadería.", "error")
        return redirect(request.referrer)

    # Cambiamos el estado como un interruptor
    if ubi.estado == 'Bloqueada':
        ubi.estado = 'Disponible'
        flash(f"Posición {ubi.codigo_unico} habilitada nuevamente.", "success")
    else:
        ubi.estado = 'Bloqueada'
        flash(f"Posición {ubi.codigo_unico} anulada por magnitud de bulto.", "info")
    
    db.session.commit()
    return redirect(request.referrer)


@app.route('/toggle_unificacion/<int:ubicacion_id>', methods=['POST'])
@login_required
def toggle_unificacion(ubicacion_id):
    if current_user.rol not in ['admin', 'supervisor', 'jefe_logistica']:
        return redirect(request.referrer)

    ubi = Ubicacion.query.get_or_404(ubicacion_id)

    if ubi.estado.startswith('Padre_'):
        # ✂️ DESUNIFICAR
        hijo_id = int(ubi.estado.split('_')[1])
        hijo = Ubicacion.query.get(hijo_id)
        ubi.estado = 'Disponible'
        if hijo:
            hijo.estado = 'Disponible'
        flash("Posiciones separadas nuevamente.", "success")
    else:
        # 🔗 UNIFICAR CON LA CONTIGUA
        # Buscamos la siguiente posición física en el mismo fierro y altura
        siguiente = Ubicacion.query.filter_by(rack_id=ubi.rack_id, nivel=ubi.nivel)\
            .filter(Ubicacion.posicion > ubi.posicion)\
            .order_by(Ubicacion.posicion.asc()).first()

        if not siguiente:
            flash("No hay una posición a la derecha para unificar.", "error")
            return redirect(request.referrer)

        # 🔥 EL FIX: Consultamos directamente a la tabla Item
        tiene_stock_ubi = Item.query.filter_by(ubicacion_id=ubi.id).first()
        tiene_stock_sig = Item.query.filter_by(ubicacion_id=siguiente.id).first()

        if tiene_stock_ubi or tiene_stock_sig:
            flash("Ambas posiciones deben estar vacías para poder unificarlas.", "error")
            return redirect(request.referrer)

        if ubi.estado != 'Disponible' or siguiente.estado != 'Disponible':
            flash("Ambas posiciones deben estar 'Disponibles' sin bloqueos.", "error")
            return redirect(request.referrer)

        # Aplicamos la Fusión usando el ID en el texto
        ubi.estado = f'Padre_{siguiente.id}'
        siguiente.estado = f'Hijo_{ubi.id}'
        flash("Posiciones fusionadas en un bloque doble.", "success")

    db.session.commit()
    return redirect(request.referrer)

@app.route('/materias_primas')
@login_required
def materias_primas():
    # Roles permitidos para este sector
    roles_ok = ['admin', 'jefe_materias_primas', 'produccion', 'operario_materias_primas','encargado']
    if current_user.rol not in roles_ok:
        flash("No tienes acceso al sector de Materias Primas.", "error")
        return redirect(url_for('home'))

    # Traemos racks de este sector
    racks = Rack.query.filter_by(sector='materias_primas').all()

    # Matemática de ocupación (Calculando huecos unificados y bloqueados)
    total_huecos = Ubicacion.query.join(Rack).filter(Rack.sector == 'materias_primas').count()
    ocupados = db.session.query(Item.ubicacion_id).join(Ubicacion).join(Rack)\
        .filter(Rack.sector == 'materias_primas').distinct().count()
    bloqueados_grises = Ubicacion.query.join(Rack).filter(Rack.sector == 'materias_primas', Ubicacion.estado == 'Bloqueada').count()
    bloqueados_hijos = Ubicacion.query.join(Rack).filter(Rack.sector == 'materias_primas', Ubicacion.estado.like('Hijo_%')).count()
    
    huecos_usables = total_huecos - bloqueados_grises - bloqueados_hijos
    vacios = huecos_usables - ocupados
    porcentaje = int((ocupados / huecos_usables) * 100) if huecos_usables > 0 else 0

    return render_template('materias_primas.html', 
                           racks=racks, total=huecos_usables, ocupados=ocupados, 
                           vacios=vacios, porcentaje=porcentaje)

@app.route('/crear_rack_materias_primas', methods=['POST'])
@login_required
def crear_rack_materias_primas():
    # 1. Validación de Seguridad
    if current_user.rol not in ['admin', 'jefe_materias_primas']:
        flash('⚠️ No tienes permisos para crear racks en este sector.', 'error')
        return redirect(url_for('materias_primas'))

    # 2. Rescate de datos del formulario
    nombre_fantasia = request.form.get('nombre').strip().upper()
    prefijo_tecnico = request.form.get('codigo_tecnico').strip().upper()
    niveles = int(request.form.get('niveles'))
    posiciones = int(request.form.get('posiciones'))
    inicio_nivel = int(request.form.get('inicio_nivel', 1))
    
    # Rescatamos el formato elegido (normal, 2digitos, 3digitos)
    formato = request.form.get('formato', 'normal')

    # 3. Creación del Rack
    nuevo_rack = Rack(
        nombre=nombre_fantasia, 
        niveles=niveles, 
        posiciones=posiciones, 
        sector='materias_primas',
        tipo_pos='secuencial', 
        multi_nivel=1
    )
    db.session.add(nuevo_rack)
    db.session.commit()

    # 4. Rango de niveles (0 o 1)
    if inicio_nivel == 0:
        rango_niveles = range(0, niveles)
    else:
        rango_niveles = range(1, niveles + 1)

    for n in rango_niveles:
        for p in range(1, posiciones + 1):
            
            # LÓGICA DE FORMATO DE DÍGITOS
            if formato == '2digitos':
                str_pos = str(p).zfill(2)
                str_nivel = str(n).zfill(2)
            elif formato == '3digitos':
                str_pos = str(p).zfill(3)
                str_nivel = str(n).zfill(3)
            else:
                str_pos = str(p)
                str_nivel = str(n)

            # 🔥 NUEVO ORDEN: PASILLO - NIVEL - POSICION - ID
            # Ejemplo si elegís 2 dígitos y el pasillo "A": A-01-05-ID8
            codigo_etiqueta = f"{prefijo_tecnico}-{str_nivel}-{str_pos}-ID{nuevo_rack.id}"
            
            nueva_ubi = Ubicacion(
                rack_id=nuevo_rack.id,
                nivel=n,
                posicion=p,
                codigo_unico=codigo_etiqueta,
                estado='Disponible'
            )
            db.session.add(nueva_ubi)

    try:
        db.session.commit()
        flash(f'✅ Rack "{nombre_fantasia}" creado (Código base: {prefijo_tecnico}).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Error al crear ubicaciones: {str(e)}', 'error')

    return redirect(url_for('materias_primas'))

import io
import pandas as pd

@app.route('/descargar_plantilla_materias_primas')
@login_required
def descargar_plantilla_materias_primas():
    # Creamos las columnas que va a leer la función de importación
    df = pd.DataFrame(columns=['SKU', 'Cantidad', 'Ubicacion', 'Lote', 'Estado', 'Observaciones'])
    
    out = io.BytesIO()
    # Usamos openpyxl para generar el .xlsx
    df.to_excel(out, index=False, engine='openpyxl')
    out.seek(0)
    
    return send_file(out, download_name="plantilla_stock_mp.xlsx", as_attachment=True)

import csv
from io import StringIO


@app.route('/importar_inventario_materias_primas', methods=['POST'])
@login_required
def importar_inventario_mp():  
    if current_user.rol not in ['admin', 'jefe_materias_primas', 'encargado']:
        flash('⚠️ Acceso denegado.', 'error')
        return redirect(url_for('materias_primas'))

    archivo = request.files.get('archivo_inventario')
    if not archivo or archivo.filename == '':
        flash('❌ No seleccionaste ningún archivo Excel.', 'error')
        return redirect(url_for('materias_primas'))

    try:
        df = pd.read_excel(archivo)
        cargados = 0
        errores = 0

        for index, row in df.iterrows():
            # 🔥 FIX 1: Forzamos mayúsculas (.upper()) para que siempre coincida con la Nómina
            sku = str(row.get('SKU', '')).strip().upper()
            cant = row.get('Cantidad', 0)
            
            # 🔥 FIX 2: Forzamos mayúsculas y limpiamos espacios de la ubicación
            cod_ubi = str(row.get('Ubicacion', '')).strip().upper().replace(' ', '')
            
            lote = str(row.get('Lote', 'General')).strip()
            estado = str(row.get('Estado', 'apto')).strip().lower()
            obs = str(row.get('Observaciones', '')).strip()

            # Evitamos nan (not a number) de pandas y filas vacías
            if pd.isna(cant) or cant == '' or int(cant) <= 0 or not sku or not cod_ubi:
                errores += 1
                continue

            # Buscamos el producto (solo de materias primas)
            producto = Producto.query.filter_by(sku=sku, sector='materias_primas').first()

            # 🔥 FIX 3: Traductor Inteligente. Busca el código ignorando el "-ID" final
            ubicacion = Ubicacion.query.join(Rack).filter(
                Ubicacion.codigo_unico.like(f"{cod_ubi}-%"), 
                Rack.sector == 'materias_primas'
            ).first()
            
            # Por si acaso alguien sí escribió el -ID en el Excel, tenemos este plan B
            if not ubicacion:
                ubicacion = Ubicacion.query.join(Rack).filter(
                    Ubicacion.codigo_unico == cod_ubi, 
                    Rack.sector == 'materias_primas'
                ).first()

            # Si encontró AMBAS cosas, procede a guardar
            if producto and ubicacion:
                nuevo_item = Item(
                    producto_id=producto.id,
                    ubicacion_id=ubicacion.id,
                    cantidad=int(cant),
                    sub_ubicacion=lote if lote and str(lote).lower() != 'nan' else 'General',
                    estado_calidad=estado if estado in ['apto', 'outlet', 'no_apto'] else 'apto',
                    observaciones=obs if obs and str(obs).lower() != 'nan' else ''
                )
                db.session.add(nuevo_item)
                
                # 🔥 FIX 4: Agregamos registro al Historial de Movimientos
                mov_historial = Movimiento(
                    tipo='ingreso',
                    sku=sku,
                    cantidad=int(cant),
                    origen="Carga Masiva (Excel)", 
                    transporte=ubicacion.codigo_unico.split('-ID')[0], 
                    usuario=current_user.username,
                    sector='materias_primas'
                )
                db.session.add(mov_historial)
                
                cargados += 1
            else:
                # Opcional: Se imprime en la consola para auditar más fácil si algo falla
                print(f"Fila {index+2} fallida -> SKU: {sku} (En DB: {producto is not None}) | UBI: {cod_ubi} (En DB: {ubicacion is not None})")
                errores += 1 

        db.session.commit()
        
        if errores > 0:
            flash(f'✅ {cargados} lotes cargados con éxito. ⚠️ {errores} filas omitidas (SKU no cargado en nómina o Ubicación inexistente).', 'success')
        else:
            flash(f'✅ Stock ingresado correctamente: {cargados} lotes.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'❌ Error al leer el Excel de Stock: {str(e)}', 'error')

    return redirect(url_for('materias_primas'))

@app.route('/nomina_materias_primas')
@login_required
def nomina_materias_primas():
    if current_user.rol not in ['admin', 'jefe_materias_primas', 'encargado']:
        flash('⚠️ Acceso denegado.', 'error')
        return redirect(url_for('home'))

    # 🔥 FILTRAMOS PARA QUE NO MUESTRE EL FANTASMA DE CAJAS VACÍAS
    productos = Producto.query.filter(
        Producto.sector == 'materias_primas', 
        Producto.sku != 'SUBDIVISION_VACIA'
    ).all()
    
    return render_template('nomina_materias_primas.html', productos=productos, sector='materias_primas')

@app.route('/pedidos_materias_primas')
@login_required
def pedidos_materias_primas():
    # Verificamos que tenga permiso (Admin, Jefe de MP o alguien de Producción)
    if current_user.rol not in ['admin', 'jefe_materias_primas', 'produccion']:
        flash('⚠️ Acceso denegado a los pedidos.', 'error')
        return redirect(url_for('materias_primas'))

    # Acá a futuro vamos a buscar los pedidos en la base de datos
    # pedidos = Pedido.query.filter_by(estado='pendiente').all()

    # Por ahora, solo renderizamos una plantilla en blanco o un mensaje
    flash('🛠️ La bandeja de pedidos está en construcción.', 'success')
    return redirect(url_for('materias_primas'))
    
    # Próximamente usaremos esto:
    # return render_template('pedidos_mp.html')

@app.route('/planificacion')
@login_required
def planificacion():
    # 🔥 1. Dejamos entrar a todo el equipo administrativo y comercial
    roles_permitidos = ['admin', 'planificacion', 'jefe_produccion', 'gerencia', 'analisis_ventas', 'administrativo', 'comercial', 'jefe_ventas', 'admin_ventas']
    
    if current_user.rol not in roles_permitidos:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('home'))

    # 🔥 2. LA REGLA DE ORO: Si no sos admin, ni de planificacion, ni gerente... sos "solo lectura"
    roles_edicion = ['admin', 'planificacion', 'jefe_produccion', 'gerencia']
    es_solo_lectura = (current_user.rol not in roles_edicion)

    cobertura_dias = request.args.get('cobertura_dias', 30, type=int)
    q = request.args.get('q', '').strip().upper() 

    # BÚSQUEDA DIRECTA EN LA BASE DE DATOS
    query_prod = Producto.query.filter(
        Producto.sector == 'logistica', 
        Producto.sku != 'SUBDIVISION_VACIA'
    )
    if q:
        busqueda = f"%{q}%"
        query_prod = query_prod.filter(db.or_(
            Producto.sku.ilike(busqueda), 
            Producto.descripcion.ilike(busqueda)
        ))
    catalogo = query_prod.all()

    # Recopilar datos
    fecha_hace_90 = datetime.now().date() - timedelta(days=90)
    ventas_90_raw = db.session.query(DetalleVenta.sku, func.sum(DetalleVenta.cantidad)).join(RegistroVenta).filter(func.date(RegistroVenta.fecha_venta) >= fecha_hace_90).group_by(DetalleVenta.sku).all()
    dict_ventas = {v[0]: v[1] for v in ventas_90_raw}

    stock_raw = db.session.query(Producto.sku, func.sum(Item.cantidad)).join(Item).join(Ubicacion).join(Rack).filter(Rack.sector == 'logistica', Item.cantidad > 0).group_by(Producto.sku).all()
    dict_stock = {s[0]: s[1] for s in stock_raw}

    wip_raw = db.session.query(OrdenProduccion.sku, func.sum(OrdenProduccion.cantidad)).filter(OrdenProduccion.estado.in_(['Pendiente', 'En Proceso'])).group_by(OrdenProduccion.sku).all()
    dict_wip = {w[0]: w[1] for w in wip_raw}

    import math
    hoy_obj = hora_argentina().date()
    datos_plan = []
    gantt_raw = []

    # Matemática MRP
    for prod in catalogo:
        ventas_90 = float(dict_ventas.get(prod.sku, 0))
        promedio_diario = ventas_90 / 90.0
        promedio_mensual = int(round(promedio_diario * 30))
        
        demanda_proyectada = promedio_diario * cobertura_dias
        stock = int(dict_stock.get(prod.sku, 0))
        wip = int(dict_wip.get(prod.sku, 0))
        
        sugerido = int(math.ceil(demanda_proyectada) - stock - wip)
        if sugerido < 0: sugerido = 0
        
        if promedio_diario > 0:
            dias_stock = int((stock + wip) / promedio_diario)
            fecha_quiebre_obj = hoy_obj + timedelta(days=dias_stock)
            fecha_quiebre = fecha_quiebre_obj.strftime('%d/%m/%Y')
            if dias_stock == 0: dias_texto = "¡Se agota HOY!"
            elif dias_stock == 1: dias_texto = "En 1 día"
            else: dias_texto = f"En {dias_stock} días"
        else:
            dias_stock = 999 
            fecha_quiebre = "∞"
            dias_texto = "Sin salida"

        if sugerido > 0:
            estado = "Falta Fabricar"
            color = "#dc2626"
            color_fecha = "#dc2626"
            bg_row = "#fff"
            color_input = "#dc2626"
        elif (stock + wip) <= (demanda_proyectada * 1.2):
            estado = "Stock Justo"
            color = "#f59e0b"
            color_fecha = "#64748b"
            bg_row = "#f8fafc"
            color_input = "#94a3b8"
        else:
            estado = "Stock Óptimo"
            color = "#16a34a"
            color_fecha = "#64748b"
            bg_row = "#f8fafc"
            color_input = "#94a3b8"
            
        datos_plan.append({
            'sku': prod.sku, 'descripcion': prod.descripcion, 'promedio_mensual': promedio_mensual,
            'stock_actual': stock, 'en_fabrica': wip, 'sugerido': sugerido,
            'estado': estado, 'color': color, 'fecha_quiebre': fecha_quiebre, 'dias_texto': dias_texto,
            'dias_num': dias_stock, 'bg_row': bg_row, 'color_fecha': color_fecha, 'color_input': color_input,
            'val_input': sugerido if sugerido > 0 else ''
        })
        
        if promedio_diario > 0:
            gantt_raw.append({'sku': prod.sku, 'dias': dias_stock})
        
    datos_plan.sort(key=lambda x: x['sugerido'], reverse=True)
    
    # 🔥 ELIMINAMOS EL LÍMITE DE 100 ACÁ

    # Gantt Quiebre
    gantt_raw.sort(key=lambda x: x['dias'])
    gantt_labels = []
    gantt_dias = []
    gantt_colores = []
    for g in gantt_raw[:15]:
        gantt_labels.append(g['sku'])
        dias_reales = g['dias']
        gantt_dias.append(min(dias_reales, 120))
        if dias_reales <= 15: gantt_colores.append('rgba(220, 38, 38, 0.85)')
        elif dias_reales <= 45: gantt_colores.append('rgba(245, 158, 11, 0.85)')
        else: gantt_colores.append('rgba(22, 163, 74, 0.85)')

    grafico_gantt = {'labels': gantt_labels, 'datos': gantt_dias, 'colores': gantt_colores}
    pedidos_ventas = PedidoCliente.query.filter_by(estado='Pendiente').order_by(PedidoCliente.fecha_creacion.asc()).all()

    # ================================================================
    # CALENDARIO DE PRODUCCIÓN ESTILO EXCEL (Próximos 15 días)
    # ================================================================
    dias_calendario = [hoy_obj + timedelta(days=i) for i in range(15)]
    dias_headers = [d.strftime('%d/%m') for d in dias_calendario]
    dias_keys = [d.strftime('%Y-%m-%d') for d in dias_calendario]

    ordenes_programadas = OrdenProduccion.query.filter(
        OrdenProduccion.fecha_planificada >= hoy_obj,
        OrdenProduccion.fecha_planificada <= dias_calendario[-1],
        OrdenProduccion.estado.in_(['Pendiente', 'En Proceso'])
    ).all()

    mapa_calendario = {}
    for ord_p in ordenes_programadas:
        sku = ord_p.sku
        f_key = ord_p.fecha_planificada.strftime('%Y-%m-%d') if ord_p.fecha_planificada else hoy_obj.strftime('%Y-%m-%d')
        if sku not in mapa_calendario:
            mapa_calendario[sku] = {'descripcion': ord_p.descripcion, 'fechas': {}}
        mapa_calendario[sku]['fechas'][f_key] = mapa_calendario[sku]['fechas'].get(f_key, 0) + ord_p.cantidad

    datos_calendario = []
    for sku, info in mapa_calendario.items():
        fila = {'sku': sku, 'descripcion': info['descripcion'], 'cantidades': []}
        for dk in dias_keys:
            fila['cantidades'].append(info['fechas'].get(dk, 0))
        datos_calendario.append(fila)
        
    datos_calendario.sort(key=lambda x: sum(x['cantidades']), reverse=True)

    return render_template('planificacion.html', 
                           planificacion=datos_plan, 
                           cobertura_dias=cobertura_dias, 
                           grafico_gantt=grafico_gantt,
                           pedidos_ventas=pedidos_ventas,
                           q=q,
                           dias_headers=dias_headers, 
                           datos_calendario=datos_calendario,
                           es_solo_lectura=es_solo_lectura) # 🔥 3. ENVIAMOS LA VARIABLE A LA PANTALLA

@app.route('/api/busqueda_planificacion_vivo')
@login_required
def api_busqueda_planificacion_vivo():
    q = request.args.get('q', '').strip().upper()
    cobertura_dias = request.args.get('cobertura_dias', 30, type=int)

    # Búsqueda ultra-rápida en Base de Datos
    query_prod = Producto.query.filter(
        Producto.sector == 'logistica', 
        Producto.sku != 'SUBDIVISION_VACIA'
    )
    if q:
        query_prod = query_prod.filter(db.or_(
            Producto.sku.ilike(f"%{q}%"), 
            Producto.descripcion.ilike(f"%{q}%")
        ))
        
    catalogo = query_prod.all()

    # Recolectamos la matemática necesaria
    fecha_hace_90 = datetime.now().date() - timedelta(days=90)
    ventas_90_raw = db.session.query(DetalleVenta.sku, func.sum(DetalleVenta.cantidad)).join(RegistroVenta).filter(func.date(RegistroVenta.fecha_venta) >= fecha_hace_90).group_by(DetalleVenta.sku).all()
    dict_ventas = {v[0]: v[1] for v in ventas_90_raw}

    stock_raw = db.session.query(Producto.sku, func.sum(Item.cantidad)).join(Item).join(Ubicacion).join(Rack).filter(Rack.sector == 'logistica', Item.cantidad > 0).group_by(Producto.sku).all()
    dict_stock = {s[0]: s[1] for s in stock_raw}

    wip_raw = db.session.query(OrdenProduccion.sku, func.sum(OrdenProduccion.cantidad)).filter(OrdenProduccion.estado.in_(['Pendiente', 'En Proceso'])).group_by(OrdenProduccion.sku).all()
    dict_wip = {w[0]: w[1] for w in wip_raw}

    import math
    hoy_obj = hora_argentina().date()
    datos_plan = []

    # Cálculos MRP
    for prod in catalogo:
        ventas_90 = float(dict_ventas.get(prod.sku, 0))
        promedio_diario = ventas_90 / 90.0
        promedio_mensual = int(round(promedio_diario * 30))
        
        demanda_proyectada = promedio_diario * cobertura_dias
        stock = int(dict_stock.get(prod.sku, 0))
        wip = int(dict_wip.get(prod.sku, 0))
        
        sugerido = int(math.ceil(demanda_proyectada) - stock - wip)
        if sugerido < 0: sugerido = 0
        
        if promedio_diario > 0:
            dias_stock = int((stock + wip) / promedio_diario)
            fecha_quiebre_obj = hoy_obj + timedelta(days=dias_stock)
            fecha_quiebre = fecha_quiebre_obj.strftime('%d/%m/%Y')
            if dias_stock == 0: dias_texto = "¡Se agota HOY!"
            elif dias_stock == 1: dias_texto = "En 1 día"
            else: dias_texto = f"En {dias_stock} días"
            dias_num = dias_stock
        else:
            fecha_quiebre = "∞"
            dias_texto = "Sin salida"
            dias_num = 9999

        if sugerido > 0:
            estado = "Falta Fabricar"
            color = "#dc2626"
            color_fecha = "#dc2626"
            bg_row = "#fff"
            color_input = "#dc2626"
        else:
            estado = "Stock Justo" if (stock + wip) <= (demanda_proyectada * 1.2) else "Stock Óptimo"
            color = "#f59e0b" if estado == "Stock Justo" else "#16a34a"
            color_fecha = "#64748b"
            bg_row = "#f8fafc"
            color_input = "#94a3b8"
            
        datos_plan.append({
            'sku': prod.sku, 'descripcion': prod.descripcion, 'promedio_mensual': promedio_mensual,
            'stock_actual': stock, 'en_fabrica': wip, 'sugerido': sugerido,
            'estado': estado, 'color': color, 'fecha_quiebre': fecha_quiebre, 'dias_texto': dias_texto,
            'dias_num': dias_num, 'bg_row': bg_row, 'color_fecha': color_fecha, 'color_input': color_input,
            'val_input': sugerido if sugerido > 0 else ''
        })
        
    datos_plan.sort(key=lambda x: x['sugerido'], reverse=True)

    # 🔥 ELIMINAMOS EL LÍMITE DE 100 ACÁ TAMBIÉN

    return jsonify({'datos': datos_plan})


@app.route('/crear_orden_fabricacion', methods=['POST'])
@login_required
def crear_orden_fabricacion():
    if current_user.rol not in ['admin', 'planificacion', 'jefe_produccion']:
        flash('⚠️ No tienes permisos para crear órdenes.', 'error')
        return redirect(url_for('planificacion'))

    sku = request.form.get('sku_terminado').strip().upper()
    cantidad = int(request.form.get('cantidad'))
    fecha_str = request.form.get('fecha_limite')
    
    # Convertimos la fecha del form
    fecha_limite = datetime.strptime(fecha_str, '%Y-%m-%d').date()

    # Usamos el NUEVO nombre de la tabla
    nueva_orden = OrdenFabricacion(
        sku_terminado=sku,
        cantidad=cantidad,
        fecha_limite=fecha_limite,
        estado='Pendiente'
    )
    
    db.session.add(nueva_orden)
    db.session.commit()
    
    flash(f'✅ Orden de fabricación para {cantidad}x {sku} lanzada a Producción.', 'success')
    return redirect(url_for('planificacion'))

@app.route('/consulta_rapida')
@login_required
def consulta_rapida():
    # 🔥 Atrapamos de dónde viene el usuario (si no dice nada, asumimos logistica)
    sector_origen = request.args.get('sector', 'logistica')
    
    # Le pasamos la variable 'sector' a tu HTML
    return render_template('consultar_ubicacion.html', sector=sector_origen)

@app.route('/procesar_consulta', methods=['POST'])
@login_required
def procesar_consulta():
    codigo_escaneado = request.form.get('codigo_escaneado', '').strip().upper()
    ubi = None
    
    # 🔥 EL FIX: Le agregamos el atajo mágico para Recepción (Igual que en el Scanner) 🔥
    if codigo_escaneado in ['RECEPCION', 'RECEPCIÓN', 'REC-LOG', 'REC-PV-LOG']:
        rack_rec = Rack.query.filter(Rack.descripcion == '[ADN_RECEPCION]').first()
        if rack_rec and rack_rec.ubicaciones:
            ubi = rack_rec.ubicaciones[0]

    # --- 1. BÚSQUEDA POR CÓDIGO EXACTO (CON O SIN ID) ---
    if not ubi:
        ubi = Ubicacion.query.filter(
            (Ubicacion.codigo_unico == codigo_escaneado) | 
            (Ubicacion.codigo_unico.like(f"{codigo_escaneado}-ID%"))
        ).first()
    
    # --- 2. TRADUCTOR DE COORDENADAS FÍSICAS (Ej: 1-1-1000) ---
    if not ubi and '-' in codigo_escaneado:
        partes = codigo_escaneado.split('-')
        if len(partes) >= 3:
            num_nivel = partes[-1]
            num_posicion = partes[-2]
            nombre_pasillo = "-".join(partes[:-2])
            
            posibles_ubis = Ubicacion.query.filter_by(posicion=num_posicion, nivel=num_nivel).all()
            for u in posibles_ubis:
                # Extraemos el prefijo real del código (Ej: de 1-1-10 saca "1")
                prefijo_real = u.codigo_unico.split('-ID')[0].rsplit('-', 2)[0]
                # Comparamos contra el prefijo extraído del escaneo
                if nombre_pasillo == prefijo_real:
                    ubi = u
                    break

    # --- 3. BÚSQUEDA POR SUBDIVISIÓN / CAJA (Ej: "1001") ---
    if not ubi:
        item_sub = Item.query.filter_by(sub_ubicacion=codigo_escaneado).first()
        if item_sub:
            ubi = item_sub.ubicacion
            
    # --- 4. BÚSQUEDA HÍBRIDA (Ej: 1-1-CAJA1) ---
    if not ubi and '-' in codigo_escaneado:
        partes = codigo_escaneado.split('-')
        if len(partes) == 3:
            posible_sub = partes[2]
            item_sub = Item.query.filter_by(sub_ubicacion=posible_sub).join(Ubicacion).filter(
                Ubicacion.posicion == partes[1]
            ).first()
            if item_sub:
                ubi = item_sub.ubicacion

    # --- RESPUESTA FINAL ---
    if ubi:
        # Si la encontramos, vamos al detalle
        return redirect(url_for('detalle_ubicacion', rack_id=ubi.rack_id, nivel=ubi.nivel, pos=ubi.posicion))
    
    # Si llegamos acá, es que realmente no existe
    flash(f"❌ La ubicación o subdivisión '{codigo_escaneado}' no reconocida.", "error")
    return redirect(request.referrer or url_for('logistica'))

@app.route('/crear_subdivision/<int:ubicacion_id>', methods=['POST'])
@login_required
def crear_subdivision(ubicacion_id):
    nombre_sub = request.form.get('nombre_subdivision', '').strip().upper()
    if not nombre_sub:
        flash("Debes ingresar un nombre para la subdivisión.", "error")
        return redirect(request.referrer)

    ubi = Ubicacion.query.get_or_404(ubicacion_id)

    # =====================================================================
    # 🛡️ BLOQUEO 1: No puede llamarse igual que el estante físico
    # =====================================================================
    codigo_estante = ubi.codigo_unico.split('-ID')[0].upper()
    if nombre_sub == codigo_estante:
        flash(f"❌ Error: La caja no puede tener el mismo nombre que la posición física ({codigo_estante}).", "error")
        return redirect(request.referrer)

    # =====================================================================
    # 🛡️ BLOQUEO 2: No pueden existir dos cajas con el mismo nombre en el sistema
    # =====================================================================
    if nombre_sub != 'GENERAL':
        caja_duplicada = Item.query.filter_by(sub_ubicacion=nombre_sub).first()
        if caja_duplicada:
            flash(f"❌ Error: Ya existe una caja o lote llamado '{nombre_sub}' en el sistema. Elegí otro nombre.", "error")
            return redirect(request.referrer)

    # 🔥 CANDADO ANTERIOR: Verificamos si hay mercadería suelta ('General')
    general_item = Item.query.filter_by(ubicacion_id=ubicacion_id, sub_ubicacion='General').first()
    
    if general_item:
        if general_item.cantidad > 0:
            # Si hay mercadería real, bloqueamos la creación de la caja
            flash("❌ No puedes crear una subdivisión aquí porque ya hay mercadería suelta ('General'). Muévela o despáchala primero.", "error")
            return redirect(request.referrer)
        else:
            # Si el 'General' estaba en 0 (quedó un fantasma vacío), lo destruimos para limpiar el estante
            db.session.delete(general_item)
            db.session.commit()
            
    # 1. Creamos el "Producto Fantasma" si no existe en este sector
    prod_vacio = Producto.query.filter_by(sku='SUBDIVISION_VACIA', sector=ubi.rack.sector).first()
    if not prod_vacio:
        prod_vacio = Producto(sku='SUBDIVISION_VACIA', descripcion='[ SUB-DIVISIÓN VACÍA ]', sector=ubi.rack.sector)
        db.session.add(prod_vacio)
        db.session.commit()

    # 2. Creamos la caja vacía en el estante
    nuevo_item = Item(
        ubicacion_id=ubicacion_id,
        producto_id=prod_vacio.id,
        cantidad=0,
        sub_ubicacion=nombre_sub,
        estado_calidad='vacia',
        observaciones='Caja libre esperando mercadería'
    )
    db.session.add(nuevo_item)
    db.session.commit()
    
    flash(f"✅ Subdivisión '{nombre_sub}' creada y lista para usar.", "success")
    return redirect(request.referrer)

@app.route('/solicitar_reposicion', methods=['POST'])
@login_required
def solicitar_reposicion():
    sku = request.form.get('sku')
    # Cantidad que le faltaba en el piso para completar el picking
    cantidad_faltante = int(request.form.get('cantidad_faltante', 0))
    origen_reserva = request.form.get('origen_reserva')
    destino_piso = request.form.get('destino_piso')

    if cantidad_faltante <= 0:
        flash("❌ Cantidad inválida para reposición.", "error")
        return redirect(request.referrer)

    # Buscamos la descripción real del producto
    producto = Producto.query.filter_by(sku=sku, sector='logistica').first()
    desc = producto.descripcion if producto else "Sin Descripción"

    # Verificamos que no haya pedido ya exactamente lo mismo para que no duplique
    tarea_activa = TareaReposicion.query.filter_by(sku=sku, estado='Pendiente').first()
    if tarea_activa:
        flash(f"⚠️ Ya hay una solicitud de reposición activa para el SKU {sku}.", "info")
        return redirect(request.referrer)

    # Creamos la tarea para el Clarckista
    nueva_tarea = TareaReposicion(
        sku=sku,
        descripcion=desc,
        cantidad_solicitada=cantidad_faltante,
        origen_sugerido=origen_reserva,
        destino_requerido=destino_piso,
        usuario_solicita=current_user.username
    )
    
    db.session.add(nueva_tarea)
    db.session.commit()
    
    flash(f"🚜 ¡Aviso enviado! Un autoelevador bajará {cantidad_faltante} unidades de {sku} al piso.", "success")
    return redirect(request.referrer)

# ==========================================
# RUTAS DEL CLARCKISTA (REPOSICIONES)
# ==========================================

@app.route('/reposiciones')
@login_required
def reposiciones():
    if current_user.rol not in ['admin', 'jefe_logistica', 'operario_logistica', 'operario']:
        flash("⚠️ No tienes permisos para ver las tareas de reposición.", "error")
        return redirect(url_for('logistica'))

    # 1. 🧠 Cerebro Espacial: Mapeamos niveles base (Esto es rápido)
    base_por_rack = {}
    for r in Rack.query.filter_by(sector='logistica').all():
        m = db.session.query(db.func.min(Ubicacion.nivel)).filter_by(rack_id=r.id).scalar()
        base_por_rack[r.id] = m if m is not None else 1

    # 2. Traemos todas las tareas de una
    tareas_pendientes = TareaReposicion.query.filter_by(estado='Pendiente').all()
    
    if not tareas_pendientes:
        return render_template('reposiciones.html', tareas=[])

    # 3. 🔥 EL FIX DE VELOCIDAD:
    # Sacamos la lista de todos los SKUs que necesitamos reponer
    skus_a_reponer = [t.sku for t in tareas_pendientes]

    # Traemos de UN SOLO GOLPE todo el stock en reserva para esos SKUs
    # Filtramos por SKUs que están en la lista, cantidad > 0 y nivel > base
    items_reserva = Item.query.join(Producto).join(Ubicacion).join(Rack).filter(
        Rack.sector == 'logistica',
        Item.cantidad > 0,
        Producto.sku.in_(skus_a_reponer)
    ).all()

    # Creamos un conjunto (Set) de los SKUs que realmente tienen stock en altura
    skus_con_reserva_real = set()
    for i in items_reserva:
        nivel_base = base_por_rack.get(i.ubicacion.rack_id, 1)
        if i.ubicacion.nivel > nivel_base:
            skus_con_reserva_real.add(i.producto_detalle.sku)

    # 4. Limpieza inteligente: Si la tarea no tiene reserva real, se va.
    tareas_a_mostrar = []
    for t in tareas_pendientes:
        if t.sku in skus_con_reserva_real:
            tareas_a_mostrar.append(t)
        else:
            # Si el Radar o alguien pidió reposición pero ya no hay nada en altura, borramos la tarea
            db.session.delete(t)
            
    db.session.commit() 

    # 5. Ordenamos por fecha para que lo más viejo salga arriba
    tareas_a_mostrar.sort(key=lambda x: x.fecha_solicitud)

    return render_template('reposiciones.html', tareas=tareas_a_mostrar)

@app.route('/procesar_reposicion/<int:tarea_id>', methods=['POST'])
@login_required
def procesar_reposicion(tarea_id):
    tarea = TareaReposicion.query.get_or_404(tarea_id)
    
    cod_origen = request.form.get('codigo_origen', '').strip().upper()
    cod_destino = request.form.get('codigo_destino', '').strip().upper()
    cantidad_mover = int(request.form.get('cantidad', 0))

    if cantidad_mover <= 0:
        flash("❌ Cantidad inválida.", "error")
        return redirect('reposiciones')

    # =========================================================================
    # 🔥 TRADUCTOR INTELIGENTE (Detecta Chapas y Cajas)
    # =========================================================================
    def buscar_ubi_y_sub(codigo):
        ubi = None
        sub_detectada = 'General'
        
        # 1. Búsqueda normal por código de chapa
        ubi = Ubicacion.query.filter(
            (Ubicacion.codigo_unico == codigo) | 
            (Ubicacion.codigo_unico.like(f"{codigo}-ID%"))
        ).first()
        
        # 2. Traductor de coordenadas físicas (Ej: 1-1-1000)
        if not ubi and '-' in codigo:
            partes = codigo.split('-')
            if len(partes) >= 3:
                num_nivel = partes[-1]
                num_posicion = partes[-2]
                nombre_pasillo = "-".join(partes[:-2])
                
                posibles = Ubicacion.query.filter_by(posicion=num_posicion, nivel=num_nivel).all()
                for p in posibles:
                    prefijo_real = p.codigo_unico.split('-ID')[0].rsplit('-', 2)[0]
                    if nombre_pasillo == prefijo_real:
                        ubi = p
                        break
        # 3. ¿Escaneó directamente el nombre de una Caja/Subdivisión?
        if not ubi:
            item_sub = Item.query.filter_by(sub_ubicacion=codigo).first()
            if item_sub:
                ubi = item_sub.ubicacion
                sub_detectada = item_sub.sub_ubicacion
                
        # 4. ¿Mezcló coordenada con caja? (Ej: R73-1-1-CAJA1)
        if not ubi and '-' in codigo:
            partes = codigo.split('-')
            if len(partes) >= 3:
                posible_sub = partes[-1] # La caja sería el último elemento
                num_pos = partes[-2]
                
                # Buscamos si existe un item con esa sub_ubicacion en esa posicion
                item_sub = Item.query.filter_by(sub_ubicacion=posible_sub).join(Ubicacion).filter(
                    Ubicacion.posicion == num_pos
                ).first()
                if item_sub:
                    ubi = item_sub.ubicacion
                    sub_detectada = item_sub.sub_ubicacion

        return ubi, sub_detectada

    # Ejecutamos el traductor para el origen y el destino
    ubi_origen, sub_origen_detectada = buscar_ubi_y_sub(cod_origen)
    ubi_destino, sub_destino_detectada = buscar_ubi_y_sub(cod_destino)

    if not ubi_origen or not ubi_destino:
        flash("❌ Códigos de ubicación o subdivisión inválidos.", "error")
        return redirect(request.referrer)

    # =========================================================================
    # 1. Extraemos el stock del origen
    # =========================================================================
    query_origen = Item.query.join(Producto).filter(
        Item.ubicacion_id == ubi_origen.id,
        Producto.sku == tarea.sku,
        Item.cantidad >= cantidad_mover
    )
    
    # Si escaneó una caja específica de origen, sacamos de ahí
    if sub_origen_detectada != 'General':
        query_origen = query_origen.filter(Item.sub_ubicacion == sub_origen_detectada)
        
    item_origen = query_origen.first()

    if not item_origen:
        flash(f"❌ Error: El SKU {tarea.sku} no está en el origen escaneado o no hay cantidad suficiente.", "error")
        return redirect(request.referrer)

    # =========================================================================
    # 2. 📦 LÓGICA DE MOVER STOCK (Directo a la caja elegida)
    # =========================================================================
    
    # Si el operario escaneó una caja destino, la usamos. Si escaneó el estante, arrastramos el nombre de la caja origen.
    if sub_destino_detectada != 'General':
        sub_ubicacion_final = sub_destino_detectada
    else:
        sub_ubicacion_final = item_origen.sub_ubicacion

    # a. Buscamos si ya existe stock en esa caja destino
    item_existente = Item.query.filter_by(
        ubicacion_id=ubi_destino.id, producto_id=item_origen.producto_id, 
        estado_calidad=item_origen.estado_calidad, sub_ubicacion=sub_ubicacion_final
    ).first()

    # b. Buscamos si existe la caja pero vacía
    fantasma_vacio = Item.query.filter_by(
        ubicacion_id=ubi_destino.id, sub_ubicacion=sub_ubicacion_final
    ).join(Producto).filter(Producto.sku == 'SUBDIVISION_VACIA').first()

    # c. Ejecución de la Fusión de Stock
    if item_existente:
        item_existente.cantidad += cantidad_mover
        if fantasma_vacio:
            db.session.delete(fantasma_vacio)

    elif fantasma_vacio:
        fantasma_vacio.producto_id = item_origen.producto_id
        fantasma_vacio.cantidad = cantidad_mover
        fantasma_vacio.estado_calidad = item_origen.estado_calidad
        fantasma_vacio.observaciones = item_origen.observaciones
        
    else:
        nuevo_item = Item(
            ubicacion_id=ubi_destino.id,
            producto_id=item_origen.producto_id,
            cantidad=cantidad_mover,
            estado_calidad=item_origen.estado_calidad,
            sub_ubicacion=sub_ubicacion_final,
            observaciones=item_origen.observaciones
        )
        db.session.add(nuevo_item)

    # =========================================================================
    # 3. 📉 RESTAR DEL ORIGEN 
    # =========================================================================
    item_origen.cantidad -= cantidad_mover
    if item_origen.cantidad <= 0:
        if item_origen.sub_ubicacion != 'General':
            prod_vacio = Producto.query.filter_by(sku='SUBDIVISION_VACIA', sector='logistica').first()
            if prod_vacio:
                item_origen.producto_id = prod_vacio.id
                item_origen.cantidad = 0
                item_origen.estado_calidad = 'vacia'
                item_origen.observaciones = 'Caja libre (vaciada por reposición)'
            else:
                db.session.delete(item_origen)
        else:
            db.session.delete(item_origen)

    # =========================================================================
    # 4. ⏱️ CÁLCULO DE TIEMPO Y REGISTRO EN HISTORIAL
    # =========================================================================
    origen_txt = f"{ubi_origen.codigo_unico.split('-ID')[0]} [Caja: {item_origen.sub_ubicacion}]" if item_origen.sub_ubicacion not in ['General', 'vacia', None] else ubi_origen.codigo_unico.split('-ID')[0]
    destino_txt = f"{ubi_destino.codigo_unico.split('-ID')[0]} [Caja: {sub_ubicacion_final}]" if sub_ubicacion_final not in ['General', 'vacia', None] else ubi_destino.codigo_unico.split('-ID')[0]

    # Calculamos cuánto tardó desde que se pidió la reposición (Formato Inteligente)
    tiempo_texto = ""
    if tarea.fecha_solicitud:
        ahora = hora_argentina()
        duracion_seg = int((ahora.replace(tzinfo=None) - tarea.fecha_solicitud.replace(tzinfo=None)).total_seconds())
        
        # Evitamos números negativos si hay algún salto raro en el reloj del servidor
        if duracion_seg < 0: 
            duracion_seg = 0

        dias = duracion_seg // 86400
        horas = (duracion_seg % 86400) // 3600
        minutos = (duracion_seg % 3600) // 60
        segundos = duracion_seg % 60
        
        # Armamos el texto según cuánto haya tardado
        if dias > 0:
            tiempo_texto = f" (Demoró: ⏱️ {dias}d {horas}h {minutos}m)"
        elif horas > 0:
            tiempo_texto = f" (Demoró: ⏱️ {horas}h {minutos}m)"
        elif minutos > 0:
            tiempo_texto = f" (Demoró: ⏱️ {minutos}m {segundos}s)"
        else:
            tiempo_texto = f" (Demoró: ⏱️ {segundos}s)"

    log_mov = Movimiento(
        tipo='movimiento',
        sku=tarea.sku,
        cantidad=cantidad_mover,
        origen=origen_txt,
        transporte=f"REPOSICIÓN A PISO: {destino_txt}{tiempo_texto}",
        usuario=current_user.username,
        sector='logistica'
    )
    db.session.add(log_mov)

    # 5. Borramos la tarea completada
    db.session.delete(tarea)
    db.session.commit()

    flash(f"✅ ¡Excelente! Se bajaron {cantidad_mover}u. de {tarea.sku} al piso (Destino: {sub_ubicacion_final}).", "success")
    return redirect(url_for('reposiciones'))

# --- 🤖 MOTOR DEL RADAR (Función Interna) ---
# --- 🤖 MOTOR DEL RADAR (Función Interna) ---
def ejecutar_radar_interno():
    """Esta función hace el trabajo sucio sin necesidad de clics ni redirects"""
    
    # =========================================================================
    # 🔥 NUEVO: AUTO-VENCIMIENTO EN SEGUNDO PLANO 🔥
    # =========================================================================
    hoy_str = hora_argentina().strftime('%Y-%m-%d')
    items_vencidos = Item.query.filter(
        Item.estado_calidad == 'apto',
        Item.fecha_vencimiento != None,
        Item.fecha_vencimiento != '',
        Item.fecha_vencimiento < hoy_str # Si la fecha ya pasó...
    ).all()
    
    if items_vencidos:
        for iv in items_vencidos:
            iv.estado_calidad = 'no_apto'
            iv.observaciones = (iv.observaciones or "") + " [VENCIDO AUTOMÁTICAMENTE]"
            
            # Dejamos la huella en el historial
            log_venc = Movimiento(
                tipo='ajuste', sku=iv.producto_detalle.sku, cantidad=iv.cantidad,
                origen=iv.ubicacion.codigo_unico.split('-ID')[0],
                transporte="SISTEMA: VENCIMIENTO AUTOMÁTICO", 
                usuario="SISTEMA 🤖", sector='logistica'
            )
            db.session.add(log_venc)
        db.session.commit()
    # =========================================================================

    base_por_rack = {}
    for r in Rack.query.filter_by(sector='logistica').all():
        m = db.session.query(db.func.min(Ubicacion.nivel)).filter_by(rack_id=r.id).scalar()
        base_por_rack[r.id] = m if m is not None else 1

    # 🔥 FIX: El radar de reposición solo mueve mercadería 'apta'
    todos_items = Item.query.join(Ubicacion).join(Rack).filter(
        Rack.sector == 'logistica', Item.cantidad > 0, Item.estado_calidad == 'apto'
    ).all()

    data_sku = {}
    for item in todos_items:
        prod = item.producto_detalle
        if not prod or prod.sku == 'SUBDIVISION_VACIA': continue
        sku = prod.sku
        nivel_base = base_por_rack.get(item.ubicacion.rack_id, 1)

        if sku not in data_sku:
            data_sku[sku] = {'producto': prod, 'piso': 0, 'reserva': []}

        if item.ubicacion.nivel <= nivel_base:
            data_sku[sku]['piso'] += item.cantidad
        else:
            data_sku[sku]['reserva'].append(item)

    # Limpieza de tareas viejas
    tareas_viejas = TareaReposicion.query.filter_by(estado='Pendiente').all()
    for t in tareas_viejas:
        info = data_sku.get(t.sku)
        if not info or len(info['reserva']) == 0 or info['piso'] > 1:
            db.session.delete(t)
    
    db.session.commit()

    # Creación/Actualización de tareas
    for sku, datos in data_sku.items():
        if datos['piso'] <= 1 and len(datos['reserva']) > 0:
            tarea_activa = TareaReposicion.query.filter_by(sku=sku, estado='Pendiente').first()
            reserva_elegida = sorted(datos['reserva'], key=lambda x: x.ubicacion.nivel)[0]
            origen_txt = f"{reserva_elegida.ubicacion.codigo_unico.split('-ID')[0]} [Caja: {reserva_elegida.sub_ubicacion}]" if reserva_elegida.sub_ubicacion not in ['General', 'vacia', None] else reserva_elegida.ubicacion.codigo_unico.split('-ID')[0]
            destino_txt = f"Pasillo {reserva_elegida.ubicacion.rack.nombre} (Piso)"

            if not tarea_activa:
                nueva_tarea = TareaReposicion(
                    sku=sku, descripcion=datos['producto'].descripcion,
                    cantidad_solicitada=reserva_elegida.cantidad,
                    origen_sugerido=origen_txt, destino_requerido=destino_txt,
                    usuario_solicita='SISTEMA 🤖'
                )
                db.session.add(nueva_tarea)
            else:
                tarea_activa.origen_sugerido = origen_txt
                tarea_activa.cantidad_solicitada = reserva_elegida.cantidad
    
    db.session.commit()
    return True

# --- 🔘 RUTA PARA EL BOTÓN MANUAL (Se mantiene por si acaso) ---
@app.route('/radar_reposicion', methods=['POST'])
@login_required
def radar_reposicion():
    if current_user.rol not in ['admin', 'jefe_logistica', 'supervisor', 'operario', 'operario_logistica']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(request.referrer)

    ejecutar_radar_interno()
    flash("📡 Radar ejecutado correctamente.", "success")
    return redirect(request.referrer)

@app.route('/guardar_orden_racks', methods=['POST'])
@login_required
def guardar_orden_racks():
    # Seguridad
    if current_user.rol not in ['admin', 'jefe_logistica', 'jefe_materias_primas', 'jefe_posventa']:
        return jsonify({'status': 'error', 'msg': 'Sin permisos'})

    # Recibimos la nueva lista ordenada directamente desde la pantalla (Javascript)
    data = request.get_json()
    orden_racks = data.get('orden', [])

    # Guardamos silenciosamente en la base de datos el nuevo orden (0, 1, 2, 3...)
    for index, rack_id in enumerate(orden_racks):
        rack = Rack.query.get(rack_id)
        if rack:
            rack.orden = index
    
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/mover_zona_rapida/<int:item_id>', methods=['POST'])
@login_required
def mover_zona_rapida(item_id):
    # 1. Permisos
    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'jefe_logistica', 'stock']:
        flash("⚠️ No tienes permisos para realizar envíos rápidos.", "error")
        return redirect(request.referrer)

    item_origen = Item.query.get_or_404(item_id)
    
    try:
        cantidad_mover = int(request.form.get('cantidad_mover', 1))
    except:
        flash("❌ Cantidad inválida.", "error")
        return redirect(request.referrer)

    # 🔥 LA MAGIA: Ahora recibimos directamente el ID del Rack destino
    id_rack_destino = request.form.get('zona_destino')
    
    if cantidad_mover <= 0 or cantidad_mover > item_origen.cantidad:
        flash("❌ La cantidad excede el stock disponible en esta ubicación.", "error")
        return redirect(request.referrer)

    # 2. Buscamos el Rack destino directamente por su ID
    rack_destino = Rack.query.get_or_404(id_rack_destino)
    
    # 3. 🛡️ POKA-YOKE: Buscamos la posición 1, nivel 1 de ese Rack (que siempre se crea por defecto)
    ubi_destino = Ubicacion.query.filter_by(rack_id=rack_destino.id).first()

    if not ubi_destino:
        flash(f"❌ El destino seleccionado no tiene ubicaciones habilitadas. Contactá al administrador.", "error")
        return redirect(request.referrer)

    # 4. 🛡️ ADUANA DE SECTORES (Si viene de Logística a Posventa)
    producto_final_id = item_origen.producto_id
    
    if item_origen.ubicacion.rack.sector != rack_destino.sector:
        # Busca si ya existe en el sector destino
        prod_destino = Producto.query.filter_by(sku=item_origen.producto_detalle.sku, sector=rack_destino.sector).first()
        if not prod_destino:
            prod_destino = Producto(
                sku=item_origen.producto_detalle.sku,
                descripcion=item_origen.producto_detalle.descripcion,
                sector=rack_destino.sector
            )
            db.session.add(prod_destino)
            db.session.flush() # Guardamos para que se le asigne un ID
        producto_final_id = prod_destino.id

    # 5. Lógica Inteligente de Estado según el "Propósito" de la zona
    estado_final = item_origen.estado_calidad
    if rack_destino.proposito in ['TALLER', 'SCRAP', 'DEVOLUCION']:
        estado_final = 'no_apto'
    elif rack_destino.proposito == 'OUTLET':
        estado_final = 'outlet'
    elif rack_destino.proposito == 'APTO':
        estado_final = 'apto'

    # 6. Fusión de Stock en Destino
    item_existente = Item.query.filter_by(
        ubicacion_id=ubi_destino.id, 
        producto_id=producto_final_id,
        estado_calidad=estado_final,
        sub_ubicacion=item_origen.sub_ubicacion
    ).first()

    if item_existente:
        item_existente.cantidad += cantidad_mover
    else:
        nuevo_item = Item(
            ubicacion_id=ubi_destino.id,
            producto_id=producto_final_id,
            cantidad=cantidad_mover,
            estado_calidad=estado_final,
            sub_ubicacion=item_origen.sub_ubicacion,
            observaciones=item_origen.observaciones
        )
        db.session.add(nuevo_item)

    # 7. Restar del Origen (Manejando Fantasmas)
    item_origen.cantidad -= cantidad_mover
    if item_origen.cantidad <= 0:
        if item_origen.sub_ubicacion != 'General':
            prod_vacio = Producto.query.filter_by(sku='SUBDIVISION_VACIA', sector=item_origen.ubicacion.rack.sector).first()
            if prod_vacio:
                item_origen.producto_id = prod_vacio.id
                item_origen.cantidad = 0
                item_origen.estado_calidad = 'vacia'
                item_origen.observaciones = 'Caja libre'
            else:
                db.session.delete(item_origen)
        else:
            db.session.delete(item_origen)

    # 8. Historial de Movimientos
    origen_txt = item_origen.ubicacion.codigo_unico.split('-ID')[0]
    destino_txt = rack_destino.nombre
    
    log_mov = Movimiento(
        tipo='movimiento',
        sku=item_origen.producto_detalle.sku,
        cantidad=cantidad_mover,
        origen=origen_txt,
        transporte=f"MOV. RÁPIDO A {destino_txt}",
        usuario=current_user.username,
        sector=item_origen.ubicacion.rack.sector
    )
    db.session.add(log_mov)

    # Si hubo cruce de sectores, anotamos el ingreso en el destino también
    if item_origen.ubicacion.rack.sector != rack_destino.sector:
        log_mov_dest = Movimiento(
            tipo='movimiento',
            sku=item_origen.producto_detalle.sku,
            cantidad=cantidad_mover,
            origen=origen_txt,
            transporte=f"RECIBIDO DESDE {item_origen.ubicacion.rack.sector.upper()}",
            usuario=current_user.username,
            sector=rack_destino.sector
        )
        db.session.add(log_mov_dest)

    db.session.commit()
    flash(f"⚡ {cantidad_mover} unidades transferidas exitosamente a {destino_txt}.", "success")
    return redirect(request.referrer)

@app.route('/importar_productos', methods=['POST'])
@login_required
def importar_productos():
    # 🔥 CANDADO DE SEGURIDAD
    if current_user.rol not in ['admin', 'jefe_logistica', 'stock']:
        flash("🚫 Acceso denegado: No tienes permisos para modificar la nómina maestra.", "error")
        return redirect(request.referrer)
    
    archivo = request.files.get('archivo_csv') # En el HTML se llama así el input, no importa que sea Excel
    if not archivo or archivo.filename == '':
        flash('❌ No se seleccionó ningún archivo.', 'error')
        return redirect(request.referrer)

    try:
        import pandas as pd
        # Leemos el Excel
        df = pd.read_excel(archivo)
        
        # Limpiamos los nombres de las columnas para que el sistema las encuentre fácil
        cols = {str(c).strip().lower().replace(' ', '').replace('_', '').replace('(', '').replace(')', ''): c for c in df.columns}
        
        def get_val(fila, posibles_nombres, default=""):
            for nombre in posibles_nombres:
                if nombre in cols:
                    val = fila.get(cols[nombre])
                    if pd.notna(val):
                        return str(val).strip()
            return default

        def get_num(fila, posibles_nombres, default=0):
            val_str = get_val(fila, posibles_nombres, "")
            try: return float(val_str) if '.' in val_str else int(val_str)
            except: return default

        productos_agregados = 0
        productos_actualizados = 0

        # Pre-cargamos la nómina para hacer las búsquedas rapidísimo
        todos_los_productos_log = Producto.query.filter_by(sector='logistica').all()
        diccionario_sku = {p.sku.upper(): p for p in todos_los_productos_log}

        for index, row in df.iterrows():
            sku = get_val(row, ['sku', 'codigo']).upper()
            desc = get_val(row, ['descripcion', 'desc', 'producto'])
            
            if not sku or not desc:
                continue # Saltamos filas vacías

            # Capturamos todos los datos nuevos
            empresa_val = get_val(row, ['empresa', 'marca'])
            ean_val = get_val(row, ['ean', 'codigodebarras'])
            familia_val = get_val(row, ['familia', 'categoria'])
            alto_val = get_num(row, ['alto', 'altocm'])
            ancho_val = get_num(row, ['ancho', 'anchocm'])
            prof_val = get_num(row, ['profundidad', 'profundidadcm', 'largo'])
            un_x_bulto_val = get_num(row, ['unidadesxbulto', 'uxb'])
            bultos_x_piso_val = get_num(row, ['bultosxpiso', 'bxpiso'])
            pisos_x_pallet_val = get_num(row, ['pisosxpallet', 'pxpallet'])
            bultos_x_pallet_val = get_num(row, ['bultosxpallet', 'bxpallet'])

            # Limpiamos el EAN si viene con formato científico (ej: 7.79E+12)
            if ean_val.endswith('.0'): ean_val = ean_val[:-2]

            # ¿Existe el producto?
            if sku in diccionario_sku:
                prod = diccionario_sku[sku]
                # Actualizamos TODO
                prod.descripcion = desc
                prod.empresa = empresa_val
                prod.ean = ean_val if ean_val else prod.ean
                prod.familia = familia_val
                prod.alto_cm = alto_val
                prod.ancho_cm = ancho_val
                prod.profundidad_cm = prof_val
                prod.unidades_x_bulto = un_x_bulto_val
                prod.bultos_x_piso = bultos_x_piso_val
                prod.pisos_x_pallet = pisos_x_pallet_val
                prod.bultos_x_pallet = bultos_x_pallet_val
                
                productos_actualizados += 1
            else:
                # Lo creamos de cero
                nuevo_prod = Producto(
                    sku=sku, descripcion=desc, sector='logistica',
                    empresa=empresa_val, ean=ean_val, familia=familia_val,
                    alto_cm=alto_val, ancho_cm=ancho_val, profundidad_cm=prof_val,
                    unidades_x_bulto=un_x_bulto_val, bultos_x_piso=bultos_x_piso_val,
                    pisos_x_pallet=pisos_x_pallet_val, bultos_x_pallet=bultos_x_pallet_val
                )
                db.session.add(nuevo_prod)
                diccionario_sku[sku] = nuevo_prod
                productos_agregados += 1

        db.session.commit()
        flash(f'✅ Catálogo Logística actualizado mediante Excel: {productos_agregados} nuevos, {productos_actualizados} actualizados.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'❌ Error crítico al importar Excel: {str(e)}', 'error')

    return redirect(request.referrer)


@app.route('/importar_inventario', methods=['POST'])
@login_required
def importar_inventario():
    if current_user.rol not in ['admin', 'jefe_logistica', 'stock', 'supervisor']: 
        flash("⚠️ No tienes permisos.", "error")
        return redirect(url_for('logistica'))

    file = request.files.get('archivo_inventario')
    if not file or file.filename == '':
        flash("❌ No se seleccionó ningún archivo.", "error")
        return redirect(request.referrer)

    try:
        # 1. GUARDAMOS EL ARCHIVO TEMPORALMENTE (Staging)
        from werkzeug.utils import secure_filename
        nombre_seguro = secure_filename(file.filename)
        temp_filename = f"temp_{uuid.uuid4().hex[:8]}_{nombre_seguro}"
        temp_path = os.path.join(app.config['CARPETA_TEMP'], temp_filename)
        file.save(temp_path)

        # 2. LO LEEMOS PARA EL SIMULACRO
        df = pd.read_excel(temp_path)
        cols = {str(c).strip().lower(): c for c in df.columns}
        
        def find_col(keyword):
            for c_low, c_orig in cols.items():
                if keyword in c_low: return c_orig
            return None

        col_sku = find_col('sku')
        col_ubi = find_col('ubicacion') or find_col('donde')
        col_cant = find_col('cant')

        correctos = []
        errores = []

        for i, row in df.iterrows():
            sku_val = str(row.get(col_sku, '')).strip().upper() if col_sku else ''
            ubi_val = str(row.get(col_ubi, '')).strip().upper() if col_ubi else ''
            
            try: cant_val = int(row.get(col_cant, 0))
            except: cant_val = 0

            # FILTRO 1: Datos vacíos o inválidos
            if not sku_val or sku_val == 'NAN' or not ubi_val or ubi_val == 'NAN' or cant_val <= 0:
                continue # Saltamos filas totalmente en blanco

            # FILTRO 2: Búsqueda en la Base de Datos para verificar existencia
            prod = Producto.query.filter_by(sku=sku_val, sector='logistica').first()
            
            ubi = Ubicacion.query.join(Rack).filter(
                Ubicacion.codigo_unico.like(f"{ubi_val}-%"), Rack.sector == 'logistica'
            ).first()
            if not ubi:
                ubi = Ubicacion.query.join(Rack).filter(
                    Ubicacion.codigo_unico == ubi_val, Rack.sector == 'logistica'
                ).first()

            # CLASIFICACIÓN (Para mostrar en la pantalla)
            if prod and ubi:
                correctos.append({
                    'sku': sku_val,
                    'descripcion': prod.descripcion,
                    'cantidad': cant_val
                })
            else:
                motivo = []
                if not prod: motivo.append(f"SKU no existe en Logística")
                if not ubi: motivo.append(f"Ubicación no encontrada")
                errores.append({
                    'sku': sku_val,
                    'ubicacion': ubi_val,
                    'motivo': " / ".join(motivo)
                })

        # Mandamos el simulacro a la pantalla visual
        return render_template('preview_stock.html', correctos=correctos, errores=errores, filename=temp_filename)

    except Exception as e:
        flash(f"❌ Error al leer el Excel: {str(e)}", "error")
        return redirect(url_for('logistica'))


@app.route('/confirmar_importacion_stock', methods=['POST'])
@login_required
def confirmar_importacion_stock():
    if current_user.rol not in ['admin', 'jefe_logistica', 'stock', 'supervisor']: 
        return redirect(url_for('logistica'))

    # Rescatamos el nombre del archivo temporal
    temp_filename = request.form.get('archivo_temporal')
    if not temp_filename:
        flash("❌ Error: No se encontró el archivo temporal de importación.", "error")
        return redirect(url_for('logistica'))

    temp_path = os.path.join(app.config['CARPETA_TEMP'], temp_filename)
    if not os.path.exists(temp_path):
        flash("❌ Error: El archivo de importación expiró o se borró. Subilo de nuevo.", "error")
        return redirect(url_for('logistica'))

    try:
        # AHORA SÍ: HACEMOS LA INYECCIÓN REAL A LA BASE DE DATOS
        df = pd.read_excel(temp_path)
        cols = {str(c).strip().lower(): c for c in df.columns}
        
        def find_col(keyword):
            for c_low, c_orig in cols.items():
                if keyword in c_low: return c_orig
            return None

        col_sku = find_col('sku')
        col_ubi = find_col('ubicacion') or find_col('donde')
        col_cant = find_col('cant')
        col_sub = find_col('sub') or find_col('caja') or find_col('lpn')
        col_lote = find_col('lote') or find_col('partida')
        col_venc = find_col('venc') or find_col('f.v')
        col_obs = find_col('obs') or find_col('falla')
        col_est = find_col('estado')

        procesados = 0

        for i, row in df.iterrows():
            sku_val = str(row.get(col_sku, '')).strip().upper() if col_sku else ''
            ubi_val = str(row.get(col_ubi, '')).strip().upper() if col_ubi else ''
            sub_val = str(row.get(col_sub, 'General')).strip() if col_sub else 'General'
            if not sub_val or sub_val.lower() == 'nan': sub_val = 'General'
            
            obs_val = str(row.get(col_obs, '')).strip() if col_obs else ''
            if not obs_val or obs_val.lower() == 'nan': obs_val = ""

            lote_val = str(row.get(col_lote, 'S/L')).strip() if col_lote else 'S/L'
            if not lote_val or lote_val.lower() == 'nan': lote_val = 'S/L'
            
            venc_raw = row.get(col_venc) if col_venc else None
            venc_str = ""
            if pd.notnull(venc_raw) and str(venc_raw).lower() != 'nan':
                try:
                    if isinstance(venc_raw, datetime):
                        venc_str = venc_raw.strftime('%Y-%m-%d')
                    else:
                        venc_str = pd.to_datetime(venc_raw, dayfirst=True).strftime('%Y-%m-%d')
                except:
                    venc_str = ""
            
            try: cant_val = int(row.get(col_cant, 0))
            except: cant_val = 0

            estado_excel = str(row.get(col_est, 'apto')).strip().lower() if col_est else 'apto'
            estado_val = 'outlet' if estado_excel in ['outlet', 'out'] else ('no_apto' if estado_excel in ['no apto', 'roto'] else 'apto')

            if not sku_val or sku_val == 'NAN' or not ubi_val or ubi_val == 'NAN' or cant_val <= 0:
                continue

            prod = Producto.query.filter_by(sku=sku_val, sector='logistica').first()
            ubi = Ubicacion.query.join(Rack).filter(Ubicacion.codigo_unico.like(f"{ubi_val}-%"), Rack.sector == 'logistica').first()
            if not ubi:
                ubi = Ubicacion.query.join(Rack).filter(Ubicacion.codigo_unico == ubi_val, Rack.sector == 'logistica').first()

            if prod and ubi:
                item_existente = Item.query.filter_by(
                    ubicacion_id=ubi.id, sub_ubicacion=sub_val, producto_id=prod.id,
                    lote=lote_val, fecha_vencimiento=venc_str, estado_calidad=estado_val
                ).first()

                if item_existente:
                    item_existente.cantidad += cant_val
                    if obs_val:
                        item_existente.observaciones = (item_existente.observaciones or "") + f" | {obs_val}"
                else:
                    caja_vacia = Item.query.filter_by(ubicacion_id=ubi.id, sub_ubicacion=sub_val).join(Producto).filter(Producto.sku == 'SUBDIVISION_VACIA').first()

                    if caja_vacia:
                        caja_vacia.producto_id = prod.id
                        caja_vacia.cantidad = cant_val
                        caja_vacia.estado_calidad = estado_val
                        caja_vacia.lote = lote_val
                        caja_vacia.fecha_vencimiento = venc_str
                        caja_vacia.observaciones = obs_val
                    else:
                        nuevo = Item(
                            producto_id=prod.id, ubicacion_id=ubi.id, cantidad=cant_val, 
                            sub_ubicacion=sub_val, estado_calidad=estado_val, observaciones=obs_val,
                            lote=lote_val, fecha_vencimiento=venc_str
                        )
                        db.session.add(nuevo)

                mov = Movimiento(
                    tipo='ingreso', sku=sku_val, cantidad=cant_val, 
                    origen=ubi.codigo_unico.split('-ID')[0], usuario=current_user.username,
                    sector='logistica', transporte=f'EXCEL ({sub_val}) - Lote: {lote_val}'
                )
                db.session.add(mov)
                procesados += 1

        db.session.commit()
        
        # 🗑️ ELIMINAMOS EL EXCEL TEMPORAL PARA NO ACUMULAR BASURA
        try: os.remove(temp_path)
        except: pass

        ejecutar_radar_interno()

        flash(f"🚀 IMPORTACIÓN CONFIRMADA: Se inyectaron {procesados} ingresos oficiales a la Base de Datos.", "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error crítico al importar a DB: {str(e)}", "error")
        
    return redirect(url_for('logistica'))

@app.route('/editar_nombre_rack/<int:rack_id>', methods=['POST'])
@login_required
def editar_nombre_rack(rack_id):
    if current_user.rol not in ['admin', 'jefe_logistica', 'jefe_materias_primas', 'jefe_posventa']:
        flash("⚠️ No tienes permisos para editar.", "error")
        return redirect(request.referrer)

    nuevo_nombre = request.form.get('nuevo_nombre', '').strip().upper()
    nuevo_deposito = request.form.get('nuevo_deposito', '').strip().upper() # 🔥 CAPTURAMOS LA CARPETA

    if not nuevo_nombre:
        flash("❌ El nombre no puede estar vacío.", "error")
        return redirect(request.referrer)

    rack = Rack.query.get_or_404(rack_id)
    nombre_viejo = rack.nombre
    rack.nombre = nuevo_nombre
    
    if nuevo_deposito:
        rack.deposito = nuevo_deposito # 🔥 LO MUDAMOS DE CARPETA
    
    try:
        db.session.commit()
        flash(f"✅ Rack actualizado y movido a la carpeta '{nuevo_deposito}'.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al editar: {str(e)}", "error")

    return redirect(request.referrer)

@app.route('/vaciar_nomina_posventa', methods=['POST'])
@login_required
def vaciar_nomina_posventa():
    # Validamos permisos (ajustá los roles según necesites)
    if current_user.rol not in ['admin', 'jefe_posventa']:
        flash("⚠️ No tienes permisos para vaciar la nómina.", "error")
        return redirect(url_for('nomina_posventa'))

    try:
        # Borramos todos los productos exclusivos del sector posventa
        Producto.query.filter_by(sector='posventa').delete()
        db.session.commit()
        flash("🗑️ Nómina de Posventa vaciada con éxito.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al vaciar la nómina: {str(e)}", "error")

    return redirect(url_for('nomina_posventa'))

@app.route('/eliminar_ubicacion/<int:ubicacion_id>', methods=['POST'])
@login_required
def eliminar_ubicacion(ubicacion_id):
    ubicacion = Ubicacion.query.get_or_404(ubicacion_id)
    sector = ubicacion.rack.sector
    
    # 🧹 PASO 1: Buscamos manualmente en la tabla Item todos los que pertenezcan a esta ubicación
    # (Así no dependemos de si existe o no el atajo "ubicacion.items")
    items_a_borrar = Item.query.filter_by(ubicacion_id=ubicacion.id).all()
    
    for item in items_a_borrar:
        db.session.delete(item)
    
    # 🗑️ PASO 2: Ahora sí, eliminamos la ubicación base con total seguridad
    db.session.delete(ubicacion)
    db.session.commit()
    
    flash('📍 Ubicación y espacios vacíos eliminados por completo.', 'success')
    
    # Redirigimos al usuario al sector que corresponda
    if sector == 'materias_primas':
        return redirect(url_for('materias_primas'))
    elif sector == 'posventa':
        return redirect(url_for('posventa'))
    else:
        return redirect(url_for('logistica'))
    
# 1. Este "Context Processor" hace que el sistema vigile las fechas en SEGUNDO PLANO
# y envíe la variable "alertas_vencimiento" a todo el sistema automáticamente.
@app.context_processor
def inyectar_vencimientos():
    if current_user.is_authenticated:
        dias_config = obtener_dias_vencimiento() # Obtenemos los días (ej: 60)
        hoy = datetime.now().date()
        limite = hoy + timedelta(days=dias_config)
        
        count = 0
        items = Item.query.filter(Item.fecha_vencimiento != None, Item.fecha_vencimiento != '').all()
        
        for item in items:
            # 🔥 FILTRO CLAVE: Solo contar si tiene cantidad mayor a 0
            if item.producto_detalle and item.producto_detalle.sku != 'SUBDIVISION_VACIA' and item.cantidad > 0:
                try:
                    fecha_v = datetime.strptime(item.fecha_vencimiento, '%Y-%m-%d').date()
                    if fecha_v <= limite:
                        count += 1
                except:
                    continue
        
        # Enviamos la cantidad de alertas Y el número de días configurado
        return dict(alertas_vencimiento=count, dias_config_alerta=dias_config)
    return dict(alertas_vencimiento=0, dias_config_alerta=30)

@app.route('/panel_vencimientos')
@login_required
def panel_vencimientos():
    # 🔥 ESCUDO PROTECTOR PARA VENCIMIENTOS
    if current_user.rol not in ['admin', 'jefe_logistica', 'supervisor', 'stock', 'consultas']:
        flash("🚫 Acceso denegado: No tienes permisos para auditar vencimientos.", "error")
        return redirect(url_for('logistica'))
    
    # 1. Obtenemos los días de alerta configurados (ej: 30, 60, 90...)
    dias_config = obtener_dias_vencimiento()
    
    # 2. Buscamos todos los ítems que tengan una fecha de vencimiento cargada
    items = Item.query.filter(Item.fecha_vencimiento != None, Item.fecha_vencimiento != '').all()
    
    hoy = datetime.now().date()
    limite = hoy + timedelta(days=dias_config)
    
    prontos_a_vencer = []
    
    for item in items:
        # 🔥 FILTROS DE SEGURIDAD: 
        # - Que el producto exista y no sea una "Caja Vacía"
        # - Que la CANTIDAD sea mayor a 0 (para evitar los "fantasmas" que mencionaste)
        if item.producto_detalle and item.producto_detalle.sku != 'SUBDIVISION_VACIA' and item.cantidad > 0:
            try:
                # Convertimos el texto de la base de datos a un objeto de fecha real
                fecha_v = datetime.strptime(item.fecha_vencimiento, '%Y-%m-%d').date()
                
                # Si la fecha está dentro del rango de alerta (o ya venció)
                if fecha_v <= limite:
                    # Calculamos cuántos días faltan (si es negativo, ya venció)
                    item.dias_restantes = (fecha_v - hoy).days
                    prontos_a_vencer.append(item)
            except (ValueError, TypeError):
                # Si la fecha está mal escrita en la DB, la ignoramos para que no explote el sistema
                continue
            
    # 3. Ordenamos: Los que vencen más pronto (o ya vencidos) arriba de todo
    prontos_a_vencer.sort(key=lambda x: x.dias_restantes)
    
    # 4. Enviamos todo al HTML, incluyendo los días configurados para mostrar en los textos
    return render_template('vencimientos.html', 
                           items=prontos_a_vencer, 
                           dias_alerta=dias_config)

def obtener_dias_vencimiento():
    try:
        config = Configuracion.query.filter_by(clave='dias_alerta_vencimiento').first()
        if not config:
            # Si la tabla existe pero no tiene el dato, lo creamos
            nuevo_config = Configuracion(clave='dias_alerta_vencimiento', valor=30)
            db.session.add(nuevo_config)
            db.session.commit()
            return 30
        return config.valor
    except Exception as e:
        # Si llegamos acá es porque la tabla NO existe en la base de datos
        print(f"⚠️ La tabla Configuracion no existe todavía: {e}")
        return 30 # Devolvemos 30 por defecto para que el alerta no desaparezca

@app.route('/configurar_vencimiento', methods=['POST'])
@login_required
def configurar_vencimiento():
    if current_user.rol not in ['admin', 'jefe_logistica']:
        flash('No tenés permiso para cambiar la configuración.', 'error')
        return redirect(request.referrer)

    nuevos_dias = request.form.get('dias', type=int)
    if nuevos_dias and nuevos_dias > 0:
        config = Configuracion.query.filter_by(clave='dias_alerta_vencimiento').first()
        if not config:
            config = Configuracion(clave='dias_alerta_vencimiento', valor=nuevos_dias)
            db.session.add(config)
        else:
            config.valor = nuevos_dias
        db.session.commit()
        flash(f'✅ Alerta configurada a {nuevos_dias} días.', 'success')
    
    return redirect(url_for('panel_vencimientos'))



    
@app.route('/bandeja_devoluciones')
@login_required
def bandeja_devoluciones():
    # 🔥 FIX DEFINITIVO: Búsqueda flexible e indestructible
    rack_dev = Rack.query.filter(
        Rack.sector == 'posventa', 
        db.or_(
            Rack.proposito.ilike('%DEVOLUCION%'), 
            Rack.nombre.ilike('%DEVOLUCION%')
        )
    ).first()
    
    devoluciones = []
    ubicacion_dev_id = None  
    
    if rack_dev:
        if rack_dev.ubicaciones:
            ubicacion_dev_id = rack_dev.ubicaciones[0].id # 🔥 Esto hace que vuelva a aparecer tu formulario en el HTML
        
        # Recorremos la mercadería usando Python (que no falla con los datos vacíos/NULL)
        for ubi in rack_dev.ubicaciones:
            for item in ubi.items_en_esta_posicion:
                # Ignoramos fantasmas y vacíos
                if item.cantidad > 0 and item.producto_detalle and item.producto_detalle.sku != 'SUBDIVISION_VACIA':
                    # Traemos todo lo que NO esté resuelto (incluye los que entraron con estado en blanco)
                    if item.estado_revision != 'resuelto':
                        devoluciones.append(item)

    # 🔥 AUTO-RESCATE (Por si en las pruebas anteriores se guardaron en otra zona) 🔥
    perdidos = Item.query.join(Ubicacion).join(Rack).filter(
        Rack.sector == 'posventa',
        Item.sub_ubicacion.like('TK-%'),
        Item.cantidad > 0
    ).all()

    ids_vistos = {d.id for d in devoluciones}
    for p in perdidos:
        if p.id not in ids_vistos and p.estado_revision != 'resuelto':
            if ubicacion_dev_id:
                p.ubicacion_id = ubicacion_dev_id # Lo mudamos físicamente a la bandeja correcta
                db.session.commit()
            devoluciones.append(p)
            ids_vistos.add(p.id)

    # Ordenar para que lo más viejo quede arriba
    devoluciones.sort(key=lambda x: x.fecha_ingreso if hasattr(x, 'fecha_ingreso') and x.fecha_ingreso else datetime.min)
                        
    return render_template('bandeja_devoluciones.html', devoluciones=devoluciones, ubicacion_dev_id=ubicacion_dev_id)

@app.route('/iniciar_revision_devolucion/<int:item_id>', methods=['POST'])
@login_required
def iniciar_revision_devolucion(item_id):
    item = Item.query.get_or_404(item_id)
    
    # Marcamos que este usuario lo está revisando para que nadie más lo toque
    item.estado_revision = 'en_revision'
    item.revisor_id = current_user.id
    
    db.session.commit()
    flash(f'Comenzaste a revisar el producto {item.producto_detalle.sku}', 'success')
    return redirect(url_for('bandeja_devoluciones'))


@app.route('/resolver_devolucion/<int:item_id>', methods=['POST'])
@login_required
def resolver_devolucion(item_id):
    item = Item.query.get_or_404(item_id)
    destino = request.form.get('destino_final') # ZVERDE, ZAMARILLA, ZBLANCA
    observaciones_nuevas = request.form.get('observaciones_finales')
    
    # Guardamos estos datos antes de modificar/borrar el item
    cantidad_movida = item.cantidad
    sku_movido = item.producto_detalle.sku
    origen_movimiento = item.ubicacion.codigo_unico.split('-ID')[0] if item.ubicacion else 'ZONA DEVOLUCIONES'

    # 1. Liberamos la revisión
    item.estado_revision = 'resuelto'
    item.revisor_id = None

    # 2. CALCULAMOS EL TIEMPO (AHORA 100% EN HORA ARGENTINA)
    tiempo_texto = ""
    if hasattr(item, 'fecha_ingreso') and item.fecha_ingreso:
        # Forzamos la hora de Argentina sin zona horaria para que la matemática no choque
        ahora = hora_argentina().replace(tzinfo=None)
        ingreso_naive = item.fecha_ingreso.replace(tzinfo=None) if hasattr(item.fecha_ingreso, 'replace') else item.fecha_ingreso
        
        duracion_segundos = int((ahora - ingreso_naive).total_seconds())
        if duracion_segundos < 0: duracion_segundos = 0 # Previene saltos negativos
        
        dias = duracion_segundos // 86400
        horas = (duracion_segundos % 86400) // 3600
        minutos = (duracion_segundos % 3600) // 60
        
        if dias > 0:
            tiempo_texto = f"{dias}d {horas}h {minutos}m"
        else:
            tiempo_texto = f"{horas}h {minutos}m"

    # 3. Armamos el detalle visual para el técnico
    obs_final = observaciones_nuevas if observaciones_nuevas else "Sin obs"
    texto_observacion = f"Admin ({tiempo_texto}): {obs_final} | " + (item.observaciones or '')
    item.observaciones = texto_observacion

    # 🔥 4. BUSCAMOS EL RACK DESTINO POR PROPÓSITO (LA MAGIA DINÁMICA) 🔥
    mapeo_propositos = {
        'ZVERDE': 'APTO',
        'ZAMARILLA': 'OUTLET',
        'ZBLANCA': 'TALLER'
    }
    prop_buscado = mapeo_propositos.get(destino)
    
    # Buscamos el rack que tenga ese propósito configurado
    rack_destino = Rack.query.filter_by(proposito=prop_buscado, sector='posventa').first()
    
    # Asignamos el estado de calidad correspondiente
    if prop_buscado == 'APTO':
        item.estado_calidad = 'apto'
    elif prop_buscado == 'OUTLET':
        item.estado_calidad = 'outlet'
    elif prop_buscado == 'TALLER':
        item.estado_calidad = 'no_apto'

    nombre_rack_mostrar = rack_destino.nombre if rack_destino else ""

    # 5. Procesamos el movimiento físico del ítem
    if rack_destino and rack_destino.ubicaciones:
        # Usamos la primera ubicación del rack (Nivel 1, Posición 1 que creamos en Ajustes)
        ubi_destino_id = rack_destino.ubicaciones[0].id
        destino_movimiento = rack_destino.ubicaciones[0].codigo_unico.split('-ID')[0]
        
        item_existente = Item.query.filter_by(
            ubicacion_id=ubi_destino_id,
            producto_id=item.producto_id,
            lote=item.lote,
            estado_calidad=item.estado_calidad,
            sub_ubicacion=item.sub_ubicacion # Agregamos esto para no mezclar cajas
        ).first()

        if item_existente and item_existente.id != item.id:
            item_existente.cantidad += item.cantidad
            # Si sumamos observaciones, las concatenamos
            item_existente.observaciones = (item_existente.observaciones or '') + " | " + item.observaciones
            db.session.delete(item)
        else:
            item.ubicacion_id = ubi_destino_id
            if hasattr(item, 'fecha_ingreso'):
                item.fecha_ingreso = hora_argentina() # 🔥 FIX: Seteamos la nueva fecha correctamente
        
        # 6. REGISTRO EN EL HISTORIAL
        log_mov = Movimiento(
            tipo='movimiento',
            sku=sku_movido,
            cantidad=cantidad_movida,
            origen=origen_movimiento,
            transporte=f"A {destino_movimiento} | ADMIN ({tiempo_texto}): {obs_final}", 
            usuario=current_user.username,
            sector='posventa'
        )
        db.session.add(log_mov)

        db.session.commit()
        flash(f'✅ Producto derivado a {nombre_rack_mostrar} y guardado en historial.', 'success')
    else:
        # Si no encontró el rack, le avisamos al usuario por qué
        db.session.rollback()
        flash(f'❌ Error: No existe una zona configurada con el propósito "{prop_buscado}". Creala en Ajustes.', 'error')

    return redirect(url_for('bandeja_devoluciones'))

@app.route('/comercial')
@login_required
def comercial():
    if current_user.rol not in ['admin', 'comercial', 'gerencia']:
        flash("⚠️ Acceso denegado.", "error")
        return redirect(url_for('home'))
    
    q = request.args.get('q', '')
    query = IncidenciaComercial.query
    
    if q:
        search = f"%{q}%"
        query = query.filter(
            db.or_(
                IncidenciaComercial.numero_reclamo.ilike(search),
                IncidenciaComercial.numero_venta.ilike(search),
                IncidenciaComercial.nombre_cliente.ilike(search),
                IncidenciaComercial.sku.ilike(search)
            )
        )
    
    # 1. Pendientes de Recepción: Solo los que están en estado 'Abierto'
    pendientes = query.filter(IncidenciaComercial.estado == 'Abierto').order_by(IncidenciaComercial.fecha_registro.desc()).all()
    
    # 2. Recibido en Depósito: Los que ya pasaron por Posventa (o están cerrados)
    recibidos = query.filter(IncidenciaComercial.estado != 'Abierto').order_by(IncidenciaComercial.fecha_registro.desc()).all()
    
    # 🔥 FIX: Traemos el catálogo de productos filtrado SOLO para el sector Posventa
    productos_pv = Producto.query.filter_by(sector='posventa').all()
    
    return render_template('comercial.html', 
                           pendientes=pendientes, 
                           recibidos=recibidos, 
                           productos=productos_pv, # <--- Enviamos solo los de Posventa
                           q=q, 
                           datetime=datetime)

@app.route('/nueva_incidencia', methods=['POST'])
@login_required
def nueva_incidencia():
    try:
        # Convertimos las fechas
        f_compra = datetime.strptime(request.form.get('fecha_compra'), '%Y-%m-%d').date() if request.form.get('fecha_compra') else None
        f_reclamo = datetime.strptime(request.form.get('fecha_reclamo'), '%Y-%m-%d').date() if request.form.get('fecha_reclamo') else None

        nueva = IncidenciaComercial(
            # BORRAMOS la línea de numero_reclamo de acá adentro
            numero_venta=request.form.get('numero_venta'),
            compra_empresa=request.form.get('compra_empresa'),
            sku=request.form.get('sku'),
            producto=request.form.get('producto'),
            cantidad=int(request.form.get('cantidad', 1)),
            fecha_compra=f_compra,
            fecha_reclamo=f_reclamo,
            quien_reporta=request.form.get('quien_reporta'),
            nombre_cliente=request.form.get('nombre_cliente'),
            lugar_entrega=request.form.get('lugar_entrega'),
            facturacion=request.form.get('facturacion'),
            motivo_devolucion=request.form.get('motivo_devolucion'),
            observaciones=request.form.get('observaciones'),
            tipo_gestion=request.form.get('tipo_gestion'),
            estado='Abierto', 
            condicion=request.form.get('condicion')
        )
        
        db.session.add(nueva)
        db.session.flush() # 🔥 MAGIA: Pedimos a la base de datos que le asigne su ID numérico
        
        # Le inventamos el nombre del ticket automáticamente (ej: TK-00001)
        nueva.numero_reclamo = f"TK-{nueva.id:05d}"
        
        db.session.commit()
        flash(f"✅ Incidencia {nueva.numero_reclamo} registrada con éxito.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al guardar: {str(e)}", "error")
        
    return redirect(url_for('comercial'))


@app.route('/posventa/recibir_ticket', methods=['POST'])
@login_required
def recibir_por_codigo():
    if current_user.rol not in ['admin', 'jefe_posventa', 'posventa', 'administrativo']:
        flash("⚠️ Acceso denegado.", "error")
        return redirect(request.referrer)

    codigo_escaneado = request.form.get('codigo_ticket', '').strip().upper()

    if not codigo_escaneado:
        flash("⚠️ El campo está vacío. Escaneá de nuevo.", "error")
        return redirect(request.referrer)

    # 🔍 BÚSQUEDA INTELIGENTE: Priorizamos siempre los que estén "Abiertos"
    ticket = IncidenciaComercial.query.filter(
        db.or_(
            IncidenciaComercial.numero_reclamo == codigo_escaneado,
            IncidenciaComercial.numero_venta == codigo_escaneado
        ),
        IncidenciaComercial.estado == 'Abierto'
    ).first()

    # Si no hay abiertos, traemos cualquiera para dar el mensaje de error correcto
    if not ticket:
        ticket = IncidenciaComercial.query.filter(
            db.or_(
                IncidenciaComercial.numero_reclamo == codigo_escaneado,
                IncidenciaComercial.numero_venta == codigo_escaneado
            )
        ).order_by(IncidenciaComercial.id.desc()).first()

    if not ticket:
        flash(f"❌ No se encontró nada con el código: {codigo_escaneado}.", "error")
        return redirect(request.referrer)

    if ticket.estado == 'Cerrado':
        flash(f"🚫 El caso {ticket.numero_reclamo} ya está CERRADO. No se puede recibir.", "error")
        return redirect(request.referrer)
        
    if ticket.estado == 'Recibido en Posventa':
        flash(f"ℹ️ El paquete de {ticket.nombre_cliente} (Ticket: {ticket.numero_reclamo}) ya fue recibido anteriormente.", "info")
        return redirect(request.referrer)

    # --- PROCESO DE RECEPCIÓN FÍSICA ---
    try:
        ticket.estado = 'Recibido en Posventa'

        # Buscamos el RACK destino por Propósito
        rack_destino = Rack.query.filter(
            Rack.sector == 'posventa', 
            db.or_(Rack.proposito == 'DEVOLUCION', Rack.nombre.ilike('%DEVOLUCION%'))
        ).first()

        if rack_destino and rack_destino.ubicaciones:
            ubi_destino = rack_destino.ubicaciones[0]
            
            # Buscamos o creamos el producto
            prod = Producto.query.filter_by(sku=ticket.sku, sector='posventa').first()
            if not prod:
                prod = Producto(sku=ticket.sku, descripcion=ticket.producto, sector='posventa')
                db.session.add(prod)
                db.session.flush()

            # Verificamos si ya está la caja
            item_existente = Item.query.filter_by(
                producto_id=prod.id, 
                ubicacion_id=ubi_destino.id,
                sub_ubicacion=ticket.numero_reclamo
            ).first()

            if item_existente:
                item_existente.cantidad += ticket.cantidad
                item_existente.estado_revision = 'pendiente'
            else:
                nuevo_item = Item(
                    producto_id=prod.id,
                    ubicacion_id=ubi_destino.id,
                    cantidad=ticket.cantidad,
                    estado_calidad='no_apto', 
                    sub_ubicacion=ticket.numero_reclamo,
                    observaciones=f"Devolución Comercial. Motivo: {ticket.motivo_devolucion}",
                    estado_revision='pendiente'
                )
                db.session.add(nuevo_item)

            # Historial de movimiento
            log_mov = Movimiento(
                tipo='ingreso',
                sku=prod.sku,
                cantidad=ticket.cantidad,
                origen="COMERCIAL (Devolución)",
                transporte=f"A {rack_destino.nombre} [Ticket: {ticket.numero_reclamo}]",
                usuario=current_user.username,
                sector='posventa'
            )
            db.session.add(log_mov)
        else:
            flash("⚠️ El ticket cambió a recibido, pero OJO: No hay zona 'DEVOLUCION' configurada para alojar la mercadería.", "warning")

        db.session.commit()
        flash(f"✅ Venta encontrada: {ticket.numero_venta} | ASIGNADO AL TICKET: {ticket.numero_reclamo} y derivado a Devoluciones.", "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error en la base de datos: {str(e)}", "error")
        
    return redirect(request.referrer)

@app.route('/posventa/control_incidencias')
@login_required
def control_incidencias_pv():
    if current_user.rol not in ['admin', 'jefe_posventa', 'posventa', 'administrativo']:
        flash("Acceso denegado.", "error")
        return redirect(url_for('home'))

    estado_filtro = request.args.get('estado', 'pendientes')
    query = IncidenciaComercial.query

    if estado_filtro == 'abiertos':
        # Solo los que todavía no recibimos
        query = query.filter(IncidenciaComercial.estado == 'Abierto')
    elif estado_filtro == 'cerrados':
        # Solo los finalizados
        query = query.filter(IncidenciaComercial.estado == 'Cerrado')
    elif estado_filtro == 'todos':
        # No filtramos nada, trae todo el historial
        pass 
    else:
        # 🚀 SOLAPA PENDIENTES: Trae los Recibidos (excluye Abiertos y Cerrados)
        query = query.filter(IncidenciaComercial.estado.notin_(['Abierto', 'Cerrado']))

    tickets = query.order_by(IncidenciaComercial.fecha_reclamo.asc()).all()
    
    return render_template('control_incidencias_pv.html', tickets=tickets, datetime=datetime, estado_filtro=estado_filtro)

@app.route('/magia_db')
def magia_db():
    try:
        db.session.execute(db.text("ALTER TABLE configuracion_produccion ADD COLUMN sku_maestro_a_medida VARCHAR(50) DEFAULT 'CORT9999'"))
        db.session.commit()
        return "<h1>✅ ¡Éxito!</h1><p>Columna 'sku_maestro_a_medida' agregada a Configuración de Producción.</p>"
    except Exception as e:
        return f"<h1>⚠️ Aviso</h1><p>Quizás ya existía o hubo un error: {str(e)}</p>"

@app.route('/api/verificar_incidencia/<sku>')
@login_required
def verificar_incidencia(sku):
    # Buscamos si hay reclamos comerciales ABIERTOS para este producto
    incidencias = IncidenciaComercial.query.filter_by(sku=sku.upper(), estado='Abierto').all()
    
    if not incidencias:
        return jsonify({'status': 'not_found'})
    
    # Si encuentra, armamos un paquete con los datos
    resultados = []
    for inc in incidencias:
        resultados.append({
            'ticket': inc.numero_reclamo,
            'venta': inc.numero_venta if inc.numero_venta else 'S/D',
            'cliente': inc.nombre_cliente if inc.nombre_cliente else 'Desconocido',
            'motivo': inc.motivo_devolucion if inc.motivo_devolucion else 'Sin detalle'
        })
        
    return jsonify({'status': 'ok', 'data': resultados})

@app.route('/importar_repuestos', methods=['POST'])
@login_required
def importar_repuestos():
    if current_user.rol not in ['admin', 'jefe_posventa', 'posventa']:
        flash("🚫 No tienes permisos para cargar repuestos.", "error")
        return redirect(request.referrer)

    archivo = request.files.get('archivo_repuestos')
    if not archivo or archivo.filename == '':
        flash('❌ No se seleccionó ningún archivo.', 'error')
        return redirect(request.referrer)

    try:
        raw_data = archivo.read()
        try:
            texto = raw_data.decode('utf-8-sig')
        except:
            texto = raw_data.decode('latin1')

        lineas = texto.splitlines()
        delimitador = ';' if ';' in lineas[0] else ','
        lector = csv.reader(lineas, delimiter=delimitador)
        next(lector, None) # Saltamos encabezado

        agregados = 0
        for fila in lector:
            # Ahora pedimos que lea al menos 2 columnas, pero idealmente 3
            if len(fila) >= 2:
                sku = str(fila[0]).strip().upper() 
                desc = str(fila[1]).strip()
                
                # 🔥 Leemos la 3ra columna. Si está vacía o no existe, le ponemos "GENERAL"
                producto_padre = str(fila[2]).strip().upper() if len(fila) > 2 and fila[2].strip() else 'GENERAL'
                
                if not sku:
                    continue

                existente = Producto.query.filter_by(sku=sku, sector='repuestos').first()
                if not existente:
                    nuevo_repuesto = Producto(
                        sku=sku,
                        descripcion=desc,
                        sector='repuestos', 
                        modelo=producto_padre  # 🔥 Guardamos la carpeta acá
                    )
                    db.session.add(nuevo_repuesto)
                    agregados += 1

        db.session.commit()
        flash(f'✅ Se han dado de alta {agregados} nuevos repuestos agrupados por producto.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'❌ Error al importar repuestos: {str(e)}', 'error')

    return redirect(request.referrer)



from flask import make_response
import io
import csv

@app.route('/descargar_plantilla_repuestos')
@login_required
def descargar_plantilla_repuestos():
    # Creamos un archivo CSV en memoria
    si = io.StringIO()
    
    # Usamos punto y coma para que el Excel en español lo separe bien en columnas
    escritor = csv.writer(si, delimiter=';')
    
    # 🔥 AHORA SON 3 ENCABEZADOS 🔥
    escritor.writerow(['SKU', 'DESCRIPCION', 'PRODUCTO_PADRE'])
    
    # Le ponemos un par de filas de ejemplo para que el operario entienda cómo se llena
    escritor.writerow(['REP-MOT-20', 'Motor Tubular 20Nm', 'CORTINAS ROLLER'])
    escritor.writerow(['REP-SOP-01', 'Soporte Metálico', 'CORTINAS ROLLER'])
    escritor.writerow(['REP-FIL-01', 'Filtro de Agua', 'CAFETERAS'])
    escritor.writerow(['REP-GEN-99', 'Tornillo T-20', 'GENERAL'])
    
    # Preparamos el archivo para que el navegador lo descargue automáticamente
    output = make_response(si.getvalue().encode('utf-8-sig')) # utf-8-sig para que no rompa los tildes
    output.headers["Content-Disposition"] = "attachment; filename=plantilla_repuestos_actualizada.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    
    return output

@app.route('/exportar_incidencias')
@login_required
def exportar_incidencias():
    if current_user.rol not in ['admin', 'jefe_posventa', 'administrativo', 'gerencia']:
        flash("⚠️ No tienes permisos para exportar incidencias.", "error")
        return redirect(request.referrer)

    estado_filtro = request.args.get('estado', 'todos')
    query = IncidenciaComercial.query

    if estado_filtro == 'abiertos':
        query = query.filter(IncidenciaComercial.estado == 'Abierto')
    elif estado_filtro == 'cerrados':
        query = query.filter(IncidenciaComercial.estado == 'Cerrado')
    elif estado_filtro == 'pendientes':
        # 🚀 MISMO FILTRO PARA EL EXCEL
        query = query.filter(IncidenciaComercial.estado.notin_(['Abierto', 'Cerrado']))

    tickets = query.order_by(IncidenciaComercial.fecha_reclamo.asc()).all()

    data = []
    for t in tickets:
        data.append({
            'Ticket': t.numero_reclamo,
            'Orden de Venta': t.numero_venta,
            'Cliente': t.nombre_cliente,
            'SKU': t.sku,
            'Producto': t.producto,
            'Cantidad': t.cantidad,
            'Fecha Compra': t.fecha_compra.strftime('%d/%m/%Y') if t.fecha_compra else 'S/D',
            'Fecha Reclamo': t.fecha_reclamo.strftime('%d/%m/%Y') if t.fecha_reclamo else 'S/D',
            'Motivo Cliente': t.motivo_devolucion,
            'Estado Actual': t.estado,
            'Resolución/Gestión': t.tipo_gestion,
            'Observaciones Internas': t.observaciones
        })

    if not data:
        flash("⚠️ No hay incidencias para exportar con este filtro.", "info")
        return redirect(request.referrer)

    import pandas as pd
    import io
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Incidencias')
        worksheet = writer.sheets['Incidencias']
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length: max_length = len(cell.value)
                except: pass
            worksheet.column_dimensions[column].width = min(max_length + 2, 50)

    output.seek(0)
    fecha_hoy = datetime.now().strftime("%d-%m-%Y")
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Reporte_Incidencias_{estado_filtro.upper()}_{fecha_hoy}.xlsx'
    )

@app.route('/api/buscar_incidencia/<codigo>')
@login_required
def api_buscar_incidencia(codigo):
    # 1. Buscamos PRIORIZANDO SIEMPRE los que estén 'Abierto'
    t = IncidenciaComercial.query.filter(
        db.or_(IncidenciaComercial.numero_reclamo == codigo, 
               IncidenciaComercial.numero_venta == codigo),
        IncidenciaComercial.estado == 'Abierto'
    ).first()
    
    # 2. Si no hay ninguno abierto, buscamos el más reciente (Para dar un mensaje de error preciso)
    if not t:
        t = IncidenciaComercial.query.filter(
            db.or_(IncidenciaComercial.numero_reclamo == codigo, 
                   IncidenciaComercial.numero_venta == codigo)
        ).order_by(IncidenciaComercial.id.desc()).first()
    
    if not t:
        return jsonify({'status': 'error', 'message': 'No se encontró ninguna venta ni ticket con ese código.'})
    
    # Validaciones de error si agarró uno que no está Abierto
    if t.estado == 'Cerrado':
        return jsonify({'status': 'error', 'message': f'El caso {t.numero_reclamo} ya está CERRADO en el sistema.'})

    if t.estado == 'Recibido en Posventa':
        return jsonify({'status': 'error', 'message': f'¡El paquete de {t.nombre_cliente} ya fue ingresado anteriormente al depósito!'})

    # Si pasa todo, lo devuelve para procesar
    return jsonify({
        'status': 'success',
        'id': t.id,
        'ticket': t.numero_reclamo,
        'cliente': t.nombre_cliente,
        'producto': t.producto,
        'venta': t.numero_venta
    })

@app.route('/api/confirmar_recepcion/<int:id>', methods=['POST'])
@login_required
def api_confirmar_recepcion(id):
    # 1. Buscamos el ticket de la incidencia del cliente
    t = IncidenciaComercial.query.get_or_404(id)
    
    try:
        # 2. Marcamos el ticket como recibido
        t.estado = 'Recibido en Posventa'
        
        # 3. 🔥 FIX: Buscamos el RACK destino por su PROPÓSITO (no por nombre)
        rack_destino = Rack.query.filter(
            Rack.sector == 'posventa',
            db.or_(Rack.proposito == 'DEVOLUCION', Rack.nombre.ilike('%DEVOLUCION%'))
        ).first()
        
        if not rack_destino:
            return jsonify({'status': 'error', 'message': 'No configuraste ninguna zona con el propósito "DEVOLUCION". Creala en los Ajustes de Posventa.'})

        # Agarramos la primera ubicación (hueco) dentro de ese Rack
        ubi_destino = Ubicacion.query.filter_by(rack_id=rack_destino.id).first()
        if not ubi_destino:
            return jsonify({'status': 'error', 'message': f'La zona {rack_destino.nombre} está creada pero no tiene posiciones.'})

        # 4. Buscamos el producto real en el catálogo usando el SKU del ticket
        prod = Producto.query.filter_by(sku=t.sku, sector='posventa').first()
        
        # 🔥 AUTO-CREACIÓN (Poka-Yoke): Si el producto no existe en Posventa, lo creamos al vuelo
        if not prod:
            prod = Producto(sku=t.sku, descripcion=t.producto, sector='posventa')
            db.session.add(prod)
            db.session.flush() # Para que nos dé su ID rápido

        # 5. Buscamos si ya existe ese producto en esa zona (Agrupamos por Ticket)
        item_existente = Item.query.filter_by(
            producto_id=prod.id, 
            ubicacion_id=ubi_destino.id,
            sub_ubicacion=t.numero_reclamo # 🔥 Guardamos el TK como caja
        ).first()

        if item_existente:
            item_existente.cantidad += t.cantidad
        else:
            nuevo_item = Item(
                producto_id=prod.id,
                ubicacion_id=ubi_destino.id,
                cantidad=t.cantidad,
                estado_calidad='no_apto', # Entra "roto" por defecto al venir de un cliente
                sub_ubicacion=t.numero_reclamo,
                observaciones=f"Devolución Comercial. Motivo: {t.motivo_devolucion}"
            )
            db.session.add(nuevo_item)

        # 6. 📝 REGISTRO EN EL HISTORIAL DE POSVENTA
        log_mov = Movimiento(
            tipo='ingreso',
            sku=prod.sku,
            cantidad=t.cantidad,
            origen="COMERCIAL (Devolución)",
            transporte=f"A {rack_destino.nombre} [Ticket: {t.numero_reclamo}]",
            usuario=current_user.username,
            sector='posventa'
        )
        db.session.add(log_mov)

        db.session.commit()
        
        # Mensaje de éxito que verá el operario en pantalla
        flash(f"✅ Paquete de {t.nombre_cliente} (Ticket {t.numero_reclamo}) ingresado a {rack_destino.nombre}", "success")
        return jsonify({'status': 'success'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/ingenieria/subir_foto/<int:id_producto>', methods=['POST'])
@login_required
def subir_foto_ingenieria(id_producto):
    # 🔥 Agregamos los roles de ingeniería al permiso
    if current_user.rol not in ['admin', 'jefe_posventa', 'posventa', 'ingenieria', 'jefe_produccion']:
        flash("🚫 No tienes permisos para modificar imágenes.", "error")
        return redirect(request.referrer)

    prod = Producto.query.get_or_404(id_producto)
    foto = request.files.get('foto_nueva')
    
    if foto and foto.filename != '':
        nombre_archivo = secure_filename(f"prod_{prod.id}_{foto.filename}")
        ruta_completa = os.path.join(app.config['CARPETA_FOTOS_REPUESTOS'], nombre_archivo)
        foto.save(ruta_completa)
        
        prod.imagen = nombre_archivo
        db.session.commit()
        flash("📸 Foto del producto actualizada con éxito.", "success")
    return redirect(request.referrer)

import os
from werkzeug.utils import secure_filename

@app.route('/posventa/subir_foto_repuesto/<int:id_producto>', methods=['POST'])
@login_required
def subir_foto_repuesto(id_producto):
    # 1. Seguridad de roles
    if current_user.rol not in ['admin', 'posventa', 'gerencia']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('nomina_repuestos'))

    # 2. Buscamos el repuesto en la base de datos
    repuesto = Producto.query.get(id_producto)
    if not repuesto:
        flash("❌ Repuesto no encontrado.", "error")
        return redirect(url_for('nomina_repuestos'))
        
    # 3. Verificamos si vino una imagen
    if 'foto' not in request.files:
        flash("⚠️ No se detectó ninguna imagen.", "error")
        return redirect(url_for('nomina_repuestos'))
        
    foto = request.files['foto']
    if foto.filename == '':
        flash("⚠️ No se seleccionó ningún archivo.", "error")
        return redirect(url_for('nomina_repuestos'))

    # 4. Guardamos la foto
    if foto:
        # Creamos la carpeta si no existe
        carpeta_uploads = os.path.join(app.root_path, 'static', 'uploads', 'repuestos')
        os.makedirs(carpeta_uploads, exist_ok=True)
        
        # Le ponemos un nombre seguro y único usando el SKU
        nombre_archivo = secure_filename(f"{repuesto.sku}_{foto.filename}")
        ruta_guardado = os.path.join(carpeta_uploads, nombre_archivo)
        
        foto.save(ruta_guardado)
        
        # Guardamos la ruta en la base de datos (ajustá 'imagen_url' si tu columna se llama distinto)
        repuesto.imagen_url = f"uploads/repuestos/{nombre_archivo}" 
        db.session.commit()
        
        flash("📸 Foto del repuesto guardada con éxito.", "success")
        
    return redirect(url_for('nomina_repuestos'))


@app.route('/ingenieria/borrar_foto/<int:id_producto>', methods=['POST'])
@login_required
def borrar_foto_ingenieria(id_producto):
    if current_user.rol not in ['admin', 'jefe_posventa', 'ingenieria', 'jefe_produccion']:
        flash("🚫 No tienes permisos.", "error")
        return redirect(request.referrer)

    prod = Producto.query.get_or_404(id_producto)
    
    if prod.imagen and prod.imagen != 'sin_foto.png':
        ruta_completa = os.path.join(app.config['CARPETA_FOTOS_REPUESTOS'], prod.imagen)
        if os.path.exists(ruta_completa):
            try: os.remove(ruta_completa)
            except: pass
                
    prod.imagen = 'sin_foto.png'
    db.session.commit()
    flash("🗑️ Foto eliminada.", "success")
    return redirect(request.referrer)


@app.route('/posventa/nomina_repuestos')
@login_required
def nomina_repuestos():
    if current_user.rol not in ['admin', 'jefe_posventa', 'posventa', 'tecnico']:
        flash("⚠️ Acceso denegado.", "error")
        return redirect(url_for('home'))

    lista_repuestos = Producto.query.filter_by(sector='repuestos').order_by(Producto.sku).all()
    
    # 🔥 LA MAGIA DEL STOCK: Hacemos que la base de datos cuente cuántos hay de cada uno en los estantes
    for rep in lista_repuestos:
        stock_real = db.session.query(db.func.sum(Item.cantidad)).filter(Item.producto_id == rep.id).scalar()
        rep.stock = stock_real if stock_real else 0
    
    # Extraemos todas las carpetas únicas para armar los botones
    carpetas = sorted(list(set([r.modelo for r in lista_repuestos if r.modelo])))
    
    return render_template('nomina_repuestos.html', repuestos=lista_repuestos, carpetas=carpetas)

@app.route('/posventa/agregar_repuesto_manual', methods=['POST'])
@login_required
def agregar_repuesto_manual():
    # 1. Seguridad
    if current_user.rol not in ['admin', 'jefe_posventa', 'posventa', 'tecnico']:
        flash("🚫 No tienes permisos para crear repuestos.", "error")
        return redirect(request.referrer)

    # 2. Capturamos los datos y los limpiamos
    sku = request.form.get('sku', '').strip().upper()
    desc = request.form.get('descripcion', '').strip()
    # Si no ponen carpeta, lo mandamos a GENERAL
    modelo = request.form.get('modelo', 'GENERAL').strip().upper()
    if not modelo:
        modelo = 'GENERAL'

    if not sku or not desc:
        flash("❌ El SKU y la descripción son obligatorios.", "error")
        return redirect(request.referrer)

    # 3. Verificamos que no exista un repuesto con ese mismo SKU
    existente = Producto.query.filter_by(sku=sku, sector='repuestos').first()
    if existente:
        flash(f"⚠️ El repuesto con SKU '{sku}' ya existe en el sistema.", "error")
        return redirect(request.referrer)

    # 4. Lo guardamos en la base de datos
    nuevo_repuesto = Producto(
        sku=sku,
        descripcion=desc,
        sector='repuestos',
        modelo=modelo
    )
    
    try:
        db.session.add(nuevo_repuesto)
        db.session.commit()
        flash(f"✅ Repuesto '{sku}' agregado manualmente con éxito.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al guardar en base de datos: {str(e)}", "error")

    return redirect(request.referrer)

@app.route('/posventa/borrar_repuesto/<int:id_producto>', methods=['POST'])
@login_required
def borrar_repuesto(id_producto):
    # 1. Seguridad: Solo Jefatura o Admin
    if current_user.rol not in ['admin', 'jefe_posventa']:
        flash("⚠️ Solo Jefatura puede eliminar repuestos del catálogo.", "error")
        return redirect(request.referrer)

    prod = Producto.query.get_or_404(id_producto)
    
    # 2. 🛡️ POKA-YOKE: Evitar borrar si tiene stock físico en algún lado
    tiene_stock = Item.query.filter_by(producto_id=id_producto).filter(Item.cantidad > 0).first()
    if tiene_stock:
        flash(f"❌ No se puede borrar '{prod.sku}' porque todavía tenés unidades físicas guardadas. Ajustá el stock a 0 primero.", "error")
        return redirect(request.referrer)

    # 3. Borramos la foto física del disco duro (si tenía) para no acumular basura
    if prod.imagen and prod.imagen != 'sin_foto.png':
        ruta_completa = os.path.join(app.config['CARPETA_FOTOS_REPUESTOS'], prod.imagen)
        if os.path.exists(ruta_completa):
            try: os.remove(ruta_completa)
            except: pass

    # 4. Destruimos el producto de la base de datos
    try:
        db.session.delete(prod)
        db.session.commit()
        flash(f"🗑️ Repuesto '{prod.sku}' eliminado por completo del catálogo.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al eliminar: {str(e)}", "error")

    return redirect(request.referrer)

@app.route('/posventa/guardar_zona', methods=['POST'])
@login_required
def guardar_zona():
    nombre = request.form.get('nombre').strip().upper()
    proposito = request.form.get('proposito')
    descripcion = request.form.get('descripcion')
    
    # 🔥 MAGIA DE COLORES DOBLES
    color1 = request.form.get('color1', '#6f42c1')
    color2 = request.form.get('color2', '#ffffff')
    estilo = request.form.get('estilo_fondo', 'solido')
    
    # Si es liso guardamos un color, si es diseño juntamos los 3 datos
    if estilo == 'solido':
        color_zona = color1
    else:
        color_zona = f"{color1}|{color2}|{estilo}"

    # Validación básica
    if not nombre or not proposito:
        flash("❌ El nombre y el propósito son obligatorios.", "error")
        return redirect(url_for('config_posventa'))

    try:
        # 2. Creamos el Rack (La Zona Operativa)
        nueva_zona = Rack(
            nombre=nombre,
            proposito=proposito,
            descripcion=descripcion,
            color=color_zona, # 🎨 Guardamos el color en la base de datos
            sector='posventa',
            niveles=1,
            posiciones=1,
            tipo='zona_operativa'
        )
        
        db.session.add(nueva_zona)
        db.session.flush() # Para obtener el ID de la zona antes del commit final

        # 3. Creamos la ubicación física automática (Nivel 1, Posición 1)
        # Esto permite que los botones de transferencia rápida tengan un destino real
        codigo = f"PV-{nombre.replace(' ', '')}-N1-P1-ID{nueva_zona.id}"
        nueva_ubi = Ubicacion(
            rack_id=nueva_zona.id,
            nivel=0,
            posicion=1,
            codigo_unico=codigo
        )
        db.session.add(nueva_ubi)
        
        # Guardamos todo en la DB
        db.session.commit()
        flash(f"✅ Zona '{nombre}' creada exitosamente.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al crear la zona: {str(e)}", "error")

    # 🔥 ESTA ES LA LÍNEA QUE FALTABA: Siempre hay que redireccionar al final
    return redirect(url_for('config_posventa'))

@app.route('/posventa/eliminar_zona/<int:id_zona>', methods=['POST'])
@login_required
def eliminar_zona(id_zona):
    # 1. Seguridad de rol
    if current_user.rol not in ['admin', 'jefe_posventa']:
        flash("⚠️ No tenés permisos para eliminar zonas.", "error")
        return redirect(url_for('config_posventa'))

    zona = Rack.query.get_or_404(id_zona)
    
    # 2. 🔥 EL ESCUDO QUE VOS QUERÉS: Verificar mercadería REAL
    # Buscamos en todas las ubicaciones de este rack si hay algo con cantidad mayor a 0
    for ubi in zona.ubicaciones:
        # Buscamos items que tengan cantidad real (>0)
        mercaderia_real = Item.query.filter_by(ubicacion_id=ubi.id).filter(Item.cantidad > 0).first()
        
        if mercaderia_real:
            # SI ENCONTRAMOS ALGO, FRENAMOS TODO.
            flash(f"❌ No se puede eliminar '{zona.nombre}'. Todavía tiene productos reales almacenados. Movelos o dejalos en 0 primero.", "error")
            return redirect(url_for('config_posventa'))

    # 3. SI LLEGAMOS ACÁ, significa que el rack está "físicamente" vacío (solo tiene registros de cantidad 0)
    try:
        # Borramos manualmente los registros de "posición libre" (cantidad 0) 
        # para que no tiren el error de 'NOT NULL constraint'
        for ubi in zona.ubicaciones:
            Item.query.filter_by(ubicacion_id=ubi.id).delete()
        
        # Ahora que limpiamos los fantasmas, borramos el rack y sus ubicaciones sin problema
        db.session.delete(zona)
        db.session.commit()
        flash(f"🗑️ Zona '{zona.nombre}' eliminada correctamente.", "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error técnico al eliminar: {str(e)}", "error")

    return redirect(url_for('config_posventa'))

# ==========================================
# CONFIGURACIÓN DE ZONAS OPERATIVAS
# ==========================================
@app.route('/posventa/configuracion')
@login_required
def config_posventa():
    # 1. Seguridad: Solo jefes y admin
    if current_user.rol not in ['admin', 'jefe_posventa']:
        flash("⚠️ Acceso denegado. Solo jefatura puede configurar zonas.", "error")
        return redirect(url_for('posventa'))

    # 2. Traemos solo las zonas dinámicas que creamos para Posventa
    zonas_activas = Rack.query.filter_by(sector='posventa', tipo='zona_operativa').all()

    # 3. Mostramos la pantalla
    return render_template('config_posventa.html', zonas=zonas_activas)

@app.route('/soltar_tarea/<int:tarea_id>')
@login_required
def soltar_tarea(tarea_id):
    tarea = TareaPicking.query.get_or_404(tarea_id)

    # Seguridad: Solo el mismo operario que lo agarró o un Admin/Jefe puede soltarlo
    if tarea.picker != current_user.username and current_user.rol not in ['admin', 'jefe_logistica', 'supervisor']:
        flash("🚫 No puedes liberar una tarea que no es tuya.", "error")
        return redirect(url_for('picking_detalle', lote=tarea.zona))

    # Guardamos los datos antes de limpiarlos
    operario_que_suelta = tarea.picker
    lote_nombre = tarea.zona
    
    # 🔥 CÁLCULO DEL TIEMPO INVERTIDO ANTES DE SOLTAR 🔥
    tiempo_texto = ""
    if tarea.hora_inicio:
        ahora = hora_argentina()
        duracion_segundos = int((ahora.replace(tzinfo=None) - tarea.hora_inicio.replace(tzinfo=None)).total_seconds())
        minutos = duracion_segundos // 60
        segundos = duracion_segundos % 60
        
        if minutos > 0:
            tiempo_texto = f" (Demoró: ⏱️ {minutos}m {segundos}s)"
        else:
            tiempo_texto = f" (Demoró: ⏱️ {segundos}s)"

    # Reseteamos los campos para que la tarea vuelva a estar libre
    tarea.picker = None
    tarea.hora_inicio = None
    
    # 📝 REGISTRO EN EL HISTORIAL (Ahora delata quién lo soltó y cuánto tardó)
    log_liberacion = Movimiento(
        tipo='anulacion',
        sku=tarea.sku,
        cantidad=tarea.cantidad,
        origen=f"Lote: {lote_nombre}",
        transporte=f"🔓 SOLTADO POR {operario_que_suelta}{tiempo_texto}",
        usuario=current_user.username,
        sector='logistica'
    )
    db.session.add(log_liberacion)
    
    db.session.commit()
    
    flash(f"🔓 El pedido de {tarea.sku} ha sido liberado. Ahora otro operario puede tomarlo.", "info")
    return redirect(url_for('picking_detalle', lote=lote_nombre))



@app.route('/exportar_produccion', methods=['POST'])
@login_required
def exportar_produccion():
    import csv
    import io
    from flask import Response
    
    if current_user.rol not in ['admin', 'supervisor_produccion','supervisor_produccio', 'produccion', 'jefe_produccion', 'planificacion']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('home'))

    fecha_desde = request.form.get('fecha_desde')
    fecha_hasta = request.form.get('fecha_hasta')

    # 🔥 FIX: Agregamos 'Anulado' para que también se baje en el Excel
    query = OrdenProduccion.query.filter(OrdenProduccion.estado.in_(['Finalizado', 'Entregado', 'Anulado']))

    if fecha_desde:
        query = query.filter(OrdenProduccion.fecha_fin >= f"{fecha_desde} 00:00:00")
    if fecha_hasta:
        query = query.filter(OrdenProduccion.fecha_fin <= f"{fecha_hasta} 23:59:59")

    ordenes = query.order_by(OrdenProduccion.fecha_fin.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';') 
    
    writer.writerow([
        'SKU', 'Descripción', 'Cantidad', 'Lote Referencia', 'Inicio', 
        'Operario Inicio', 'Fin', 'Operario Fin', 'Tiempo Total (Seg)', 'Estado'
    ])

    for o in ordenes:
        inicio_str = o.fecha_inicio.strftime('%d/%m/%Y %H:%M:%S') if o.fecha_inicio else 'Manual'
        fin_str = o.fecha_fin.strftime('%d/%m/%Y %H:%M:%S') if o.fecha_fin else 'Manual'
        
        segundos_totales = 0
        if o.fecha_inicio and o.fecha_fin:
            segundos_totales = int((o.fecha_fin - o.fecha_inicio).total_seconds())

        writer.writerow([
            o.sku, o.descripcion or 'S/D', o.cantidad, o.lote_referencia or '-', 
            inicio_str, o.operario_inicio or '-', fin_str, o.operario_fin or '-',    
            segundos_totales, o.estado
        ])

    response = Response(output.getvalue().encode('utf-8-sig'), mimetype='text/csv')
    response.headers["Content-Disposition"] = f"attachment; filename=reporte_detallado_fabrica_{fecha_desde}.csv"
    
    return response




@app.route('/ventas')
@login_required
def ventas():
    if current_user.rol not in ['admin', 'planificacion', 'comercial', 'jefe_logistica', 'jefe_ventas', 'admin_ventas']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('home'))

    # 1. Atrapamos los filtros primero para usarlos EN TODO EL TABLERO
    anio_actual_real = datetime.now().date().year
    anio_ver = request.args.get('anio_filtro', anio_actual_real, type=int)
    es_anio_actual = (anio_ver == anio_actual_real)

    q_sku = request.args.get('q_sku', '').strip().upper()
    q_desc = request.args.get('q_desc', '').strip().upper()
    q_fecha_desde = request.args.get('q_fecha_desde', '').strip()
    q_fecha_hasta = request.args.get('q_fecha_hasta', '').strip()
    page = request.args.get('page', 1, type=int)

    años_disponibles = db.session.query(func.extract('year', RegistroVenta.fecha_venta)).distinct().all()
    lista_años = sorted([int(a[0]) for a in años_disponibles if a[0]], reverse=True)
    hoy_obj = datetime.now().date()

    # =========================================================================
    # 🔥 MAGIA: BASES DE BÚSQUEDA GLOBALES (Afectan gráficos, números y rankings)
    # =========================================================================
    query_base = DetalleVenta.query.join(RegistroVenta)
    ranking_base = db.session.query(DetalleVenta.sku, func.sum(DetalleVenta.cantidad).label('total_cant')).join(RegistroVenta)
    suma_base = db.session.query(func.sum(DetalleVenta.cantidad)).join(RegistroVenta)

    # Si buscás "DRAX", se lo aplicamos A TODO
    if q_sku:
        query_base = query_base.filter(DetalleVenta.sku.ilike(f"%{q_sku}%"))
        ranking_base = ranking_base.filter(DetalleVenta.sku.ilike(f"%{q_sku}%"))
        suma_base = suma_base.filter(DetalleVenta.sku.ilike(f"%{q_sku}%"))
    if q_desc:
        query_base = query_base.filter(DetalleVenta.descripcion.ilike(f"%{q_desc}%"))
        ranking_base = ranking_base.filter(DetalleVenta.descripcion.ilike(f"%{q_desc}%"))
        suma_base = suma_base.filter(DetalleVenta.descripcion.ilike(f"%{q_desc}%"))

    # Inicializamos variables
    v_hoy = v_semana = v_mes = v_anio = 0
    labels_7d = []
    datos_7d = []
    dict_meses = {}

    # 3. Lógica Historial vs Año Específico
    if anio_ver != 0:
        inicio_anio = datetime(anio_ver, 1, 1).date()
        fin_anio = datetime(anio_ver, 12, 31).date()
        
        q_anio = suma_base.filter(RegistroVenta.fecha_venta >= inicio_anio, RegistroVenta.fecha_venta <= fin_anio)
        v_anio = q_anio.scalar() or 0
        
        ventas_anio_raw = query_base.with_entities(RegistroVenta.fecha_venta, DetalleVenta.cantidad).filter(RegistroVenta.fecha_venta >= inicio_anio, RegistroVenta.fecha_venta <= fin_anio).all()
        for v in ventas_anio_raw:
            mes = v.fecha_venta.month
            dict_meses[mes] = dict_meses.get(mes, 0) + v.cantidad

        if es_anio_actual:
            inicio_mes = hoy_obj.replace(day=1).strftime('%Y-%m-%d')
            inicio_semana = (hoy_obj - timedelta(days=hoy_obj.weekday())).strftime('%Y-%m-%d')
            hoy_str = hoy_obj.strftime('%Y-%m-%d')

            v_hoy = suma_base.filter(func.date(RegistroVenta.fecha_venta) == hoy_str).scalar() or 0
            v_semana = suma_base.filter(func.date(RegistroVenta.fecha_venta) >= inicio_semana).scalar() or 0
            v_mes = suma_base.filter(func.date(RegistroVenta.fecha_venta) >= inicio_mes).scalar() or 0
            
            fecha_hace_7 = hoy_obj - timedelta(days=6)
            ventas_7d_raw = query_base.with_entities(RegistroVenta.fecha_venta, DetalleVenta.cantidad).filter(func.date(RegistroVenta.fecha_venta) >= fecha_hace_7.strftime('%Y-%m-%d')).all()
            dict_7d = {}
            for v in ventas_7d_raw:
                fecha_str = v.fecha_venta.strftime('%d/%m') 
                dict_7d[fecha_str] = dict_7d.get(fecha_str, 0) + v.cantidad

            for i in range(7):
                dia = fecha_hace_7 + timedelta(days=i)
                dia_str = dia.strftime('%d/%m')
                labels_7d.append(dia_str)
                datos_7d.append(dict_7d.get(dia_str, 0))

    else:
        v_anio = suma_base.scalar() or 0
        ventas_anio_raw = query_base.with_entities(RegistroVenta.fecha_venta, DetalleVenta.cantidad).all()
        for v in ventas_anio_raw:
            mes = v.fecha_venta.month
            dict_meses[mes] = dict_meses.get(mes, 0) + v.cantidad

    # 4. RANKINGS (Afectados por el filtro)
    ranking_query_act = ranking_base
    if anio_ver != 0:
        ranking_query_act = ranking_query_act.filter(RegistroVenta.fecha_venta >= inicio_anio, RegistroVenta.fecha_venta <= fin_anio)
        
    ranking_mes = ranking_query_act.group_by(DetalleVenta.sku).order_by(func.sum(DetalleVenta.cantidad).desc()).limit(20).all()
    
    ranking_semana = []
    if es_anio_actual:
        inicio_semana_str = (hoy_obj - timedelta(days=hoy_obj.weekday())).strftime('%Y-%m-%d')
        ranking_semana = ranking_base.filter(func.date(RegistroVenta.fecha_venta) >= inicio_semana_str)\
                         .group_by(DetalleVenta.sku).order_by(func.sum(DetalleVenta.cantidad).desc()).limit(20).all()

    # 5. Gráficos
    nombres_meses = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    datos_anio = [dict_meses.get(i, 0) for i in range(1, 13)]
    graficos_data = {'labels_7d': labels_7d, 'datos_7d': datos_7d, 'labels_anio': nombres_meses, 'datos_anio': datos_anio}

    # 6. STOCK FÍSICO CENTRALIZADO (Afectado por el filtro)
    catalogo_maestro = Producto.query.filter(Producto.sector == 'logistica')
    if q_sku: catalogo_maestro = catalogo_maestro.filter(Producto.sku.ilike(f"%{q_sku}%"))
    if q_desc: catalogo_maestro = catalogo_maestro.filter(Producto.descripcion.ilike(f"%{q_desc}%"))
        
    catalogo_maestro = catalogo_maestro.order_by(Producto.sku.asc()).all()
    
    stock_real = db.session.query(
        Producto.sku, func.sum(Item.cantidad)
    ).join(Item, Producto.id == Item.producto_id)\
     .join(Ubicacion, Item.ubicacion_id == Ubicacion.id)\
     .join(Rack, Ubicacion.rack_id == Rack.id)\
     .filter(Rack.sector == 'logistica', Item.cantidad > 0)
     
    if q_sku: stock_real = stock_real.filter(Producto.sku.ilike(f"%{q_sku}%"))
    if q_desc: stock_real = stock_real.filter(Producto.descripcion.ilike(f"%{q_desc}%"))
     
    stock_real = stock_real.group_by(Producto.sku).all()
    dict_stock = {s[0]: s[1] for s in stock_real}
    
    stock_disponible = [
        {'sku': p.sku, 'descripcion': p.descripcion, 'total_stock': dict_stock.get(p.sku, 0)} 
        for p in catalogo_maestro
    ]

    # 7. Tabla Paginada
    if q_fecha_desde: query_base = query_base.filter(func.date(RegistroVenta.fecha_venta) >= q_fecha_desde)
    if q_fecha_hasta: query_base = query_base.filter(func.date(RegistroVenta.fecha_venta) <= q_fecha_hasta)

    movimientos_paginados = query_base.order_by(RegistroVenta.fecha_venta.desc(), RegistroVenta.id.desc()).paginate(page=page, per_page=20, error_out=False)

    return render_template('ventas.html', 
                           movimientos=movimientos_paginados, 
                           stats={'hoy': int(v_hoy), 'semana': int(v_semana), 'mes': int(v_mes), 'anio': int(v_anio)}, 
                           lista_años=lista_años, anio_ver=anio_ver,
                           graficos_data=graficos_data, 
                           ranking_mes=ranking_mes, ranking_semana=ranking_semana, 
                           stock_disponible=stock_disponible,
                           q_sku=q_sku, q_desc=q_desc, q_fecha_desde=q_fecha_desde, q_fecha_hasta=q_fecha_hasta, 
                           es_anio_actual=es_anio_actual)

@app.route('/descargar_plantilla_ventas')
@login_required
def descargar_plantilla_ventas():
    import pandas as pd
    import io
    from flask import send_file
    
    # 🔥 PLANTILLA SÚPER SIMPLIFICADA (Solo 3 columnas)
    data = {
        'FECHA': ['2026-04-13'],
        'SKU': ['CORT0001'],
        'CANTIDAD': [5]
    }
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='PlantillaVentas')
    output.seek(0)
    
    return send_file(output, 
                     download_name="plantilla_ventas_express.xlsx", 
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/importar_ventas', methods=['POST'])
@login_required
def importar_ventas():
    if 'archivo_ventas' not in request.files:
        flash("No se seleccionó ningún archivo", "error")
        return redirect(url_for('ventas'))
    
    file = request.files['archivo_ventas']
    
    try:
        import pandas as pd
        df = pd.read_excel(file)
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        ventas_nuevas = 0
        filas_omitidas = 0
        
        for index, fila in df.iterrows():
            sku = str(fila.get('SKU', '')).strip().upper()
            cant_raw = fila.get('CANTIDAD')

            if not sku or sku == 'NAN' or pd.isna(cant_raw) or str(cant_raw).strip() == '':
                filas_omitidas += 1
                continue 
                
            try:
                cant = int(cant_raw)
                if cant <= 0: 
                    filas_omitidas += 1
                    continue
            except:
                filas_omitidas += 1
                continue

            try:
                fecha_excel = pd.to_datetime(fila['FECHA']).date()
            except:
                fecha_excel = datetime.now().date()
                
            # 🔥 FIX: Buscamos el nombre del producto en LOGÍSTICA
            prod_maestro = Producto.query.filter_by(sku=sku, sector='logistica').first()
            descripcion_final = prod_maestro.descripcion if prod_maestro else "⚠️ SKU NO CARGADO EN LOGÍSTICA"

            nueva_venta = RegistroVenta(
                nro_comprobante=f"VTA-{datetime.now().strftime('%y%m%d%H%M%S')}-{index}",
                fecha_venta=fecha_excel,
                cliente="Venta Directa",
                canal="General",
                total_venta=0.0
            )
            db.session.add(nueva_venta)
            db.session.flush() 
            
            detalle = DetalleVenta(
                venta_id=nueva_venta.id,
                sku=sku,
                descripcion=descripcion_final,
                cantidad=cant,
                precio_unitario=0.0,
                subtotal=0.0
            )
            db.session.add(detalle)
            ventas_nuevas += 1
            
        db.session.commit()
        msg = f"✅ Se procesaron {ventas_nuevas} ventas."
        if filas_omitidas > 0:
            msg += f" ⚠️ Se omitieron {filas_omitidas} filas por falta de cantidad o SKU."
        flash(msg, "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al procesar el Excel: {str(e)}", "error")
        
    return redirect(url_for('ventas'))

@app.route('/reset_analisis_ventas')
@login_required
def reset_analisis_ventas():
    # Seguridad: Solo el admin puede resetear la contabilidad
    if current_user.rol != 'admin':
        flash("🚫 No tienes permisos para realizar esta acción.", "error")
        return redirect(url_for('ventas'))

    try:
        # Borramos primero los detalles (hijos) y luego los registros (padres)
        DetalleVenta.query.delete()
        RegistroVenta.query.delete()
        
        db.session.commit()
        flash("✅ Análisis de ventas reseteado con éxito. El tablero está en cero.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al intentar resetear: {str(e)}", "error")
        
    return redirect(url_for('ventas'))

@app.route('/reset_historial_produccion')
@login_required
def reset_historial_produccion():
    # Seguridad: Solo el administrador general puede borrar el historial
    if current_user.rol != 'admin':
        flash("🚫 No tienes permisos para realizar esta acción.", "error")
        return redirect(url_for('historial_produccion'))

    try:
        # 🔥 EL FILTRO INTELIGENTE: Solo borra lo que ya está terminado o entregado
        OrdenProduccion.query.filter(
            OrdenProduccion.estado.in_(['Finalizado', 'Entregado'])
        ).delete()
        
        db.session.commit()
        flash("✅ Historial de Producción limpiado con éxito. (Las órdenes activas se mantienen).", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al intentar limpiar el historial: {str(e)}", "error")
        
    return redirect(url_for('historial_produccion'))

@app.route('/borrar_ventas_total')
@login_required
def borrar_ventas_total_url():
    # 🔒 SEGURIDAD: Solo el Admin puede gatillar esto desde la URL
    if current_user.rol != 'admin':
        return "<h1>🚫 Acceso Denegado</h1><p>No tenés permisos para ejecutar esta acción.</p>", 403

    try:
        # 1. Limpiamos las tablas (Detalle primero, luego Cabecera)
        db.session.query(DetalleVenta).delete()
        db.session.query(RegistroVenta).delete()
        
        # 2. Guardamos los cambios
        db.session.commit()
        
        # 3. Respuesta simple en pantalla
        return """
            <div style="font-family: sans-serif; text-align: center; margin-top: 50px;">
                <h1 style="color: #10b981;">✅ ¡Historial de Ventas Borrado!</h1>
                <p>Las tablas han sido vaciadas con éxito.</p>
                <a href="/ventas" style="display: inline-block; padding: 10px 20px; background: #3b82f6; color: white; text-decoration: none; border-radius: 5px;">Volver al Panel de Ventas</a>
            </div>
        """
    except Exception as e:
        db.session.rollback()
        return f"<h1>❌ Error al intentar vaciar las ventas</h1><p>{str(e)}</p>", 500


@app.route('/ingenieria')
@login_required
def ingenieria():
    # Seguridad: Solo Admin e Ingeniería
    if current_user.rol not in ['admin', 'ingenieria', 'jefe_produccion']:
        flash("🚫 Acceso denegado al módulo de Ingeniería.", "error")
        return redirect(url_for('home'))

    # 🔍 1. Capturamos la búsqueda (Sin forzar .upper() aquí)
    q = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)

    query = Producto.query.filter(
        Producto.sector == 'logistica', 
        Producto.sku != 'SUBDIVISION_VACIA'
    )

    # 🔥 2. LA SOLUCIÓN: Usamos func.lower para ignorar mayúsculas/minúsculas
    if q:
        search = f"%{q.lower()}%"
        query = query.filter(db.or_(
            func.lower(Producto.sku).like(search),
            func.lower(Producto.descripcion).like(search)
        ))

    # El resto de la función (pagination, render_template) queda igual...
    pagination = query.order_by(Producto.sku.asc()).paginate(page=page, per_page=20, error_out=False)
    
    return render_template('ingenieria.html', 
                           productos=pagination.items, 
                           pagination=pagination, 
                           q=q)

@app.route('/ingenieria/receta/<int:prod_id>')
@login_required
def ver_receta(prod_id):
    producto = Producto.query.get_or_404(prod_id)
    # 🔥 CAMBIO AQUÍ: Agregamos el .order_by(Receta.orden.asc())
    componentes = Receta.query.filter_by(producto_final_id=prod_id).order_by(Receta.orden.asc()).all()
    
    return render_template('editar_receta.html', producto=producto, componentes=componentes)

@app.route('/ingenieria/agregar_insumo', methods=['POST'])
@login_required
def agregar_insumo_receta():
    prod_final_id = request.form.get('producto_final_id')
    insumo_id = request.form.get('insumo_id')
    cantidad = request.form.get('cantidad', 1)
    unidad_medida = request.form.get('unidad_medida', 'Unidades')
    
    # 🔥 ATRAPAMOS LA FÓRMULA Y LA CONDICIÓN 🔥
    formula = request.form.get('formula', '').strip()
    condicion = request.form.get('condicion', '').strip()

    existente = Receta.query.filter_by(producto_final_id=prod_final_id, insumo_id=insumo_id).first()
    if existente:
        existente.cantidad_necesaria = cantidad
        existente.unidad_medida = unidad_medida
        existente.formula = formula
        existente.condicion = condicion
    else:
        nueva_linea = Receta(
            producto_final_id=prod_final_id, 
            insumo_id=insumo_id, 
            cantidad_necesaria=cantidad,
            unidad_medida=unidad_medida,
            formula=formula,
            condicion=condicion
        )
        db.session.add(nueva_linea)
    
    db.session.commit()
    flash("✅ Insumo configurado en la receta.", "success")
    return redirect(url_for('ver_receta', prod_id=prod_final_id))

@app.route('/ingenieria/borrar_insumo/<int:receta_id>', methods=['POST'])
@login_required
def borrar_insumo_receta(receta_id):
    item = Receta.query.get_or_404(receta_id)
    prod_id = item.producto_final_id
    db.session.delete(item)
    db.session.commit()
    flash("🗑️ Insumo quitado de la receta.", "info")
    return redirect(url_for('ver_receta', prod_id=prod_id))

@app.route('/ventas/subir_nomina', methods=['POST'])
@login_required
def subir_nomina_productos():
    if current_user.rol not in ['admin', 'jefe_ventas']:
        flash("🚫 No tienes permisos para actualizar la nómina.", "error")
        return redirect(url_for('ventas'))

    archivo = request.files.get('archivo_nomina')
    if not archivo:
        flash("❌ No seleccionaste ningún archivo.", "error")
        return redirect(url_for('ventas'))

    try:
        df = pd.read_excel(archivo)
        df.columns = [c.upper().strip() for c in df.columns]

        if 'SKU' not in df.columns or 'DESCRIPCION' not in df.columns:
            flash("❌ El Excel debe tener las columnas SKU y DESCRIPCION.", "error")
            return redirect(url_for('ventas'))

        cont_nuevos = 0
        cont_actualizados = 0

        for index, row in df.iterrows():
            sku_val = str(row['SKU']).strip().upper()
            desc_val = str(row['DESCRIPCION']).strip().upper()
            
            # 🔥 LÓGICA ANTI-DECIMALES PARA EL EAN
            ean_raw = row.get('EAN')
            ean_val = None
            if pd.notnull(ean_raw) and str(ean_raw).strip() != '':
                try:
                    # Si viene como 779...0, lo pasa a int y luego a string
                    ean_val = str(int(float(ean_raw))).strip()
                except ValueError:
                    # Si tiene letras por algún motivo, lo deja como texto
                    ean_val = str(ean_raw).strip()

            if sku_val and desc_val:
                # Buscamos en el sector ventas
                prod = Producto.query.filter_by(sku=sku_val, sector='ventas').first()
                if prod:
                    prod.descripcion = desc_val
                    prod.ean = ean_val
                    cont_actualizados += 1
                else:
                    nuevo_prod = Producto(sku=sku_val, descripcion=desc_val, ean=ean_val, sector='ventas')
                    db.session.add(nuevo_prod)
                    cont_nuevos += 1

        db.session.commit()
        flash(f"✅ Nómina de Ventas actualizada: {cont_nuevos} nuevos, {cont_actualizados} actualizados.", "success")
        return redirect(url_for('ventas'))

    except Exception as e:
        db.session.rollback()
        print(f"Error técnico: {str(e)}")
        flash(f"❌ Error al procesar nómina: {str(e)}", "error")
        return redirect(url_for('ventas'))

@app.route('/ventas/descargar_plantilla_nomina')
@login_required
def descargar_plantilla_nomina():
    import pandas as pd
    import io
    from flask import send_file
    
    # Agregamos la columna EAN
    data = {
        'SKU': ['CORT0001', 'CORT0002'],
        'EAN': ['7791234567890', '7790987654321'], # Ejemplo de código de barras
        'DESCRIPCION': ['Cortina Roller Blackout Blanca 120x150', 'Cortina Roller Sunscreen Gris 5% 140x200']
    }
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='NominaProductos')
    output.seek(0)
    
    return send_file(output, 
                     download_name="plantilla_nomina_maestra_ean.xlsx", 
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/ventas/nomina')
@login_required
def nomina_ventas():
    # Permisos para el área comercial
    if current_user.rol not in ['admin', 'planificacion', 'comercial', 'jefe_ventas', 'admin_ventas', 'vendedor']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('home'))
        
    q = request.args.get('q', '').strip().upper()
    page = request.args.get('page', 1, type=int)

    # 🔥 FIX: Ahora Ventas mira directamente la bóveda de LOGÍSTICA
    query = Producto.query.filter(
        Producto.sector == 'logistica', 
        Producto.sku != 'SUBDIVISION_VACIA'
    )

    if q:
        query = query.filter(db.or_(
            Producto.sku.ilike(f"{q}%"),
            Producto.descripcion.ilike(f"%{q}%")
        ))

    # Mostramos de a 50 productos
    pagination = query.order_by(Producto.sku.asc()).paginate(page=page, per_page=50, error_out=False)
    
    return render_template('nomina_ventas.html', productos=pagination.items, pagination=pagination, q=q)

@app.route('/api/alertas_fabrica')
@login_required
def alertas_fabrica():
    # Solo a Logística y Admin le interesa esto
    if current_user.rol not in ['admin', 'jefe_logistica', 'supervisor', 'stock', 'operario']:
        return {"listos": 0}
    
    try:
        # 🔥 FILTRO CLAVE: Solo contamos pedidos finalizados que vinieron de Logística
        # Ignoramos lo que diga "Planificación" para no molestar al depósito.
        cantidad_listos = OrdenProduccion.query.filter_by(
            estado='Finalizado', 
            origen_pedido='Logística'
        ).count()
        
        return {"listos": cantidad_listos}
    except:
        return {"listos": 0}

# 🔥 LA ASPIRADORA DE FANTASMAS (Ruta oculta de un solo uso) 🔥
@app.route('/limpiar_fantasmas')
@login_required
def limpiar_fantasmas():
    if current_user.rol == 'admin':
        # Buscamos todas las órdenes que quedaron trabadas en 'Finalizado'
        fantasmas = OrdenProduccion.query.filter_by(estado='Finalizado').all()
        contador = 0
        
        for f in fantasmas:
            f.estado = 'Anulado' # Los pasamos a anulado para que el radar los suelte
            contador += 1
            
        db.session.commit()
        return f"<h1>👻 ¡Éxito! Se aspiraron {contador} fantasmas.</h1> <p>Ya podés volver a Logística y el cartel habrá desaparecido.</p>"
    else:
        return "No tenés permiso para usar la aspiradora."

@app.route('/api/datos_etiqueta/<int:orden_id>')
@login_required
def datos_etiqueta(orden_id):
    orden = OrdenProduccion.query.get_or_404(orden_id)
    producto = Producto.query.filter_by(sku=orden.sku, sector='logistica').first()
    
    if not producto:
        return jsonify({'status': 'error', 'message': 'SKU no encontrado'}), 404
        
    return jsonify({
        'status': 'ok',
        'sku': producto.sku,
        'ean': producto.ean if producto.ean else '0000000000000',
        'descripcion': producto.descripcion,
        'cantidad': orden.cantidad  # 🔥 AGREGAMOS ESTA LÍNEA
    })

@app.route('/anular_produccion_planificada/<int:orden_id>', methods=['POST'])
@login_required
def anular_produccion_planificada(orden_id):
    roles_autorizados = ['admin', 'supervisor_produccion', 'supervisor_produccio', 'jefe_produccion', 'planificacion', 'encargado']
    if current_user.rol not in roles_autorizados:
        flash("🚫 No tienes permisos.", "error")
        return redirect(request.referrer)

    orden = OrdenProduccion.query.get_or_404(orden_id)
    
    # 🔥 ESCUDO DE TITANIO: Bloquea si ya se fabricó o se entregó
    if orden.estado in ['Finalizado', 'Entregado']:
        flash("❌ No se puede anular un pedido que ya fue fabricado o entregado a Logística.", "error")
        return redirect(request.referrer)

    motivo = request.form.get('motivo_anulacion', '').strip()
    texto_motivo = f" - Motivo: {motivo}" if motivo else ""

    # Vinculación con Ventas
    if orden.origen_pedido == 'Ventas' and orden.lote_referencia.startswith('PED-'):
        try:
            pedido_id = int(orden.lote_referencia.replace('PED-', ''))
            pedido = PedidoCliente.query.get(pedido_id)
            if pedido:
                pedido.estado = 'Anulado'
        except: pass

    orden.estado = 'Anulado'
    orden.descripcion = f"{orden.descripcion} (ANULADO POR {current_user.username.upper()}{texto_motivo})"
    orden.fecha_fin = hora_argentina()
    orden.operario_fin = current_user.username 
    
    db.session.commit()
    flash(f"🚫 Orden {orden.sku} anulada.", "success")
    return redirect(request.referrer)

@app.route('/admin/eliminar_movimiento/<int:mov_id>', methods=['POST'])
@login_required
def admin_eliminar_movimiento(mov_id):
    # 🔥 CANDADO ESTRICTO: SOLO EL ADMIN REAL
    if current_user.rol != 'admin':
        flash("🚫 Acceso denegado: Solo el Administrador puede borrar registros históricos.", "error")
        return redirect(request.referrer)

    movimiento = Movimiento.query.get_or_404(mov_id)

    try:
        db.session.delete(movimiento)
        db.session.commit()
        flash("🗑️ Registro eliminado del historial permanentemente.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al borrar: {str(e)}", "error")

    return redirect(request.referrer)

@app.route('/api/datos_etiqueta_manual/<path:sku>')
@login_required
def datos_etiqueta_manual(sku):
    # Buscamos el producto exacto por SKU
    producto = Producto.query.filter_by(sku=sku.upper(), sector='logistica').first()
    
    if not producto:
        return jsonify({'status': 'error', 'message': 'SKU no encontrado'}), 404
        
    return jsonify({
        'status': 'ok',
        'sku': producto.sku,
        'ean': producto.ean if producto.ean else '0000000000000',
        'descripcion': producto.descripcion,
        'cantidad': 1 # La cantidad la vamos a modificar desde el botón del HTML
    })

@app.route('/api/etiquetas_rapidas')
@login_required
def etiquetas_rapidas():
    q = request.args.get('q', '').strip().upper()
    
    if not q:
        return jsonify([])

    # 🚀 Busca solo en Logística, solo CORT/DRAX y trae máximo 20 para no tildar la PC
    busqueda = f"%{q}%"
    productos = Producto.query.filter(
        Producto.sector == 'logistica',
        db.or_(Producto.sku.like('CORT%'), Producto.sku.like('DRAX%')),
        db.or_(Producto.sku.like(busqueda), Producto.descripcion.ilike(busqueda))
    ).limit(20).all()

    return jsonify([{"sku": p.sku, "descripcion": p.descripcion} for p in productos])

@app.route('/api/buscar_todo_produccion')
@login_required
def buscar_todo_produccion():
    # 🚀 Esta API no tiene límite de 20. Trae todo el catálogo CORT/DRAX.
    q = request.args.get('q', '').strip().upper()
    if not q:
        return jsonify([])

    busqueda = f"%{q}%"
    productos = Producto.query.filter(
        Producto.sector == 'logistica',
        db.or_(Producto.sku.like('CORT%'), Producto.sku.like('DRAX%')),
        db.or_(Producto.sku.like(busqueda), Producto.descripcion.ilike(busqueda))
    ).order_by(Producto.sku.asc()).all()

    return jsonify([{"sku": p.sku, "descripcion": p.descripcion} for p in productos])

from collections import defaultdict

# ==========================================
# MÓDULO FINANCIERO: COSTOS M.O.D. (ARGENTINA)
# ==========================================
@app.route('/produccion/guardar_costo_mod', methods=['POST'])
@login_required
def guardar_costo_mod():
    # Solo Gerencia o Admin pueden tocar sueldos
    if current_user.rol not in ['admin', 'gerencia']:
        flash("🚫 Acceso denegado a configuración financiera.", "error")
        return redirect(request.referrer)

    try:
        sueldo_neto = int(request.form.get('sueldo_neto', 0))
        horas_mensuales = int(request.form.get('horas_mensuales', 0)) # 🔥 Agregamos las horas
    except ValueError:
        sueldo_neto = 0
        horas_mensuales = 0

    cambios = False

    # 1. Guardar Sueldo Neto
    if sueldo_neto > 0:
        config_sueldo = Configuracion.query.filter_by(clave='sueldo_neto_operario').first()
        if not config_sueldo:
            config_sueldo = Configuracion(clave='sueldo_neto_operario', valor=sueldo_neto)
            db.session.add(config_sueldo)
        else:
            config_sueldo.valor = sueldo_neto
        cambios = True

    # 2. Guardar Horas Mensuales
    if horas_mensuales > 0:
        config_horas = Configuracion.query.filter_by(clave='horas_mensuales_operario').first()
        if not config_horas:
            config_horas = Configuracion(clave='horas_mensuales_operario', valor=horas_mensuales)
            db.session.add(config_horas)
        else:
            config_horas.valor = horas_mensuales
        cambios = True

    if cambios:
        db.session.commit()
        flash(f"💵 Parámetros de MOD actualizados (Sueldo: ${sueldo_neto:,.0f} | Horas: {horas_mensuales}hs).", "success")
        
    return redirect(request.referrer)

from collections import defaultdict

# 🔥 REEMPLAZÁ TU RUTA DE REPORTE DIARIO POR ESTA VERSIÓN FINANCIERA 🔥
@app.route('/produccion/reporte_diario')
@login_required
def reporte_produccion_diario():
    if current_user.rol not in ['admin', 'jefe_produccion', 'planificacion', 'supervisor_produccion', 'supervisor_produccio', 'gerencia']:
        flash("🚫 Acceso denegado al módulo de reportes.", "error")
        return redirect(url_for('home'))

    fecha_str = request.args.get('fecha', hora_argentina().strftime('%Y-%m-%d'))
    
    ordenes = OrdenProduccion.query.filter(
        OrdenProduccion.estado.in_(['Finalizado', 'Entregado']),
        db.func.date(OrdenProduccion.fecha_fin) == fecha_str
    ).order_by(OrdenProduccion.fecha_fin.asc()).all()

    # --- 1. LEER CONFIGURACIONES ---
    def get_conf(clave, default_val):
        c = Configuracion.query.filter_by(clave=clave).first()
        return c.valor if c else default_val

    s_neto_op = get_conf('sueldo_neto_operario', 0)
    cant_op = get_conf('cant_operarios', 1)
    s_neto_enc = get_conf('sueldo_neto_encargado', 0)
    pct_enc = get_conf('pct_encargado', 0) / 100.0 # Lo pasamos a decimal (ej: 0.20)
    horas_mes = get_conf('horas_mensuales', 160)

    # --- 2. MATEMÁTICA LABORAL ARGENTINA ---
    def calc_costo_empleador(neto):
        if neto <= 0: return 0
        bruto = neto / 0.80 # Asumiendo 20% retenciones empleado
        # Bruto + 26% Cargas + SAC + Cargas s/SAC + Vacaciones = ~41.3% extra sobre el bruto
        return bruto * 1.413 

    # Costo de todos los operarios juntos
    costo_mes_ops = calc_costo_empleador(s_neto_op) * cant_op
    # Costo del porcentaje del encargado
    costo_mes_enc = calc_costo_empleador(s_neto_enc) * pct_enc
    
    # Costo total mensual de la planta
    costo_total_mes = costo_mes_ops + costo_mes_enc

    # Costos fraccionados
    costo_hora = costo_total_mes / horas_mes if horas_mes > 0 else 0
    costo_segundo = costo_hora / 3600 if costo_hora > 0 else 0

    # --- 3. VARIABLES TOTALES ---
    total_unidades = 0
    total_segundos = 0
    total_costo_dia = 0.0
    from collections import defaultdict
    resumen_sku = defaultdict(lambda: {'descripcion': '', 'cantidad': 0, 'segundos': 0, 'ordenes': 0, 'costo': 0.0})

    for o in ordenes:
        total_unidades += o.cantidad
        duracion_seg = 0
        if o.fecha_inicio and o.fecha_fin:
            duracion_seg = int((o.fecha_fin - o.fecha_inicio).total_seconds())
            if duracion_seg < 0: duracion_seg = 0
            total_segundos += duracion_seg

        # 🔥 ACÁ SE APLICA EL COSTO MULTIPLICADO 🔥
        costo_orden = duracion_seg * costo_segundo
        total_costo_dia += costo_orden

        resumen_sku[o.sku]['descripcion'] = o.descripcion or 'S/D'
        resumen_sku[o.sku]['cantidad'] += o.cantidad
        resumen_sku[o.sku]['segundos'] += duracion_seg
        resumen_sku[o.sku]['ordenes'] += 1
        resumen_sku[o.sku]['costo'] += costo_orden

    horas_totales = total_segundos // 3600
    minutos_totales = (total_segundos % 3600) // 60
    tiempo_total_str = f"{horas_totales}h {minutos_totales}m"

    # Paquete de datos para mostrar en el HTML
    configs_dict = {
        'sueldo_neto_operario': s_neto_op, 'cant_operarios': cant_op,
        'sueldo_neto_encargado': s_neto_enc, 'pct_encargado': int(pct_enc * 100),
        'horas_mensuales': horas_mes
    }
    
    desglose = {
        'costo_total_mes': costo_total_mes,
        'costo_hora': costo_hora,
        'costo_segundo': costo_segundo
    }

    return render_template('reporte_produccion.html', 
                           fecha=fecha_str, ordenes=ordenes, resumen_sku=dict(resumen_sku),
                           total_unidades=total_unidades, tiempo_total_str=tiempo_total_str,
                           total_segundos=total_segundos, total_costo_dia=total_costo_dia,
                           desglose=desglose, configs=configs_dict, datetime=datetime)

@app.route('/produccion/guardar_config_costos', methods=['POST'])
@login_required
def guardar_config_costos():
    if current_user.rol not in ['admin', 'gerencia', 'jefe_produccion']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(request.referrer)

    # Atrapamos los datos del formulario (usamos float para que acepte decimales sin romperse)
    datos = {
        'sueldo_neto_operario': float(request.form.get('sueldo_neto', 0)),
        'cant_operarios': int(float(request.form.get('cant_operarios', 1))),
        'sueldo_neto_encargado': float(request.form.get('sueldo_encargado', 0)),
        'pct_encargado': float(request.form.get('pct_encargado', 0)),
        'horas_mensuales': int(float(request.form.get('horas_mensuales', 160)))
    }

    # Los guardamos en la tabla de configuraciones
    for clave, valor in datos.items():
        conf = Configuracion.query.filter_by(clave=clave).first()
        if not conf:
            conf = Configuracion(clave=clave, valor=valor)
            db.session.add(conf)
        else:
            conf.valor = valor
    
    db.session.commit()
    flash("⚙️ Parámetros de costo de planta actualizados.", "success")
    return redirect(request.referrer)


@app.route('/exportar_ventas')
@login_required
def exportar_ventas():
    # Seguridad: Mismos roles que pueden ver el dashboard
    if current_user.rol not in ['admin', 'planificacion', 'comercial', 'jefe_logistica', 'jefe_ventas', 'admin_ventas']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(request.referrer or url_for('home'))

    # 1. Atrapamos los filtros actuales
    q_sku = request.args.get('q_sku', '').strip().upper()
    q_desc = request.args.get('q_desc', '').strip().upper()
    q_fecha_desde = request.args.get('q_fecha_desde', '').strip()
    q_fecha_hasta = request.args.get('q_fecha_hasta', '').strip()
    anio_filtro = request.args.get('anio_filtro', 0, type=int)

    # 2. Armamos la consulta base
    query_mov = DetalleVenta.query.join(RegistroVenta)

    if anio_filtro != 0:
        inicio_anio = datetime(anio_filtro, 1, 1).date()
        fin_anio = datetime(anio_filtro, 12, 31).date()
        query_mov = query_mov.filter(RegistroVenta.fecha_venta >= inicio_anio, RegistroVenta.fecha_venta <= fin_anio)

    if q_sku:
        query_mov = query_mov.filter(DetalleVenta.sku.ilike(f"%{q_sku}%"))
    if q_desc:
        query_mov = query_mov.filter(DetalleVenta.descripcion.ilike(f"%{q_desc}%"))
    if q_fecha_desde:
        query_mov = query_mov.filter(func.date(RegistroVenta.fecha_venta) >= q_fecha_desde)
    if q_fecha_hasta:
        query_mov = query_mov.filter(func.date(RegistroVenta.fecha_venta) <= q_fecha_hasta)

    movimientos = query_mov.order_by(RegistroVenta.fecha_venta.desc(), RegistroVenta.id.desc()).all()

    if not movimientos:
        flash("⚠️ No hay datos para exportar con estos filtros.", "info")
        return redirect(request.referrer)

    # 3. Preparamos los datos para el Excel con el extractor de medidas
    data = []
    for item in movimientos:
        desc_limpia = item.descripcion.upper()
        ancho = "-"
        alto = "-"
        
        # 🔍 EXTRACTOR INTELIGENTE: Busca patrones como "120X150" o "120.5 x 150"
        match = re.search(r'(\d+(?:[\.,]\d+)?)\s*[X]\s*(\d+(?:[\.,]\d+)?)', desc_limpia)
        if match:
            ancho = match.group(1).replace(',', '.')
            alto = match.group(2).replace(',', '.')

        data.append({
            'Fecha': item.venta.fecha_venta.strftime('%d/%m/%Y'),
            'Comprobante': item.venta.nro_comprobante,
            'Canal': item.venta.canal,
            'SKU': item.sku,
            'Descripción': item.descripcion,
            'Ancho (cm)': ancho,  # 🔥 COLUMNA NUEVA
            'Alto (cm)': alto,    # 🔥 COLUMNA NUEVA
            'Cantidad': item.cantidad
        })

    # 4. Generamos el archivo Excel
    df = pd.DataFrame(data)
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Reporte de Ventas')
        
        # Ajuste de diseño
        worksheet = writer.sheets['Reporte de Ventas']
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length: 
                        max_length = len(cell.value)
                except: pass
            worksheet.column_dimensions[column].width = min(max_length + 2, 60)

    output.seek(0)
    fecha_hoy = datetime.now().strftime("%d-%m-%Y")
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Reporte_Ventas_Detallado_{fecha_hoy}.xlsx'
    )

from datetime import timedelta

@app.route('/mantenimiento')
@login_required
def mantenimiento():
    if current_user.rol not in ['admin', 'jefe_logistica', 'supervisor']:
        flash("🚫 Acceso denegado. Solo jefatura puede gestionar mantenimientos.", "error")
        return redirect(url_for('logistica'))
    
    # 🔥 Leemos los días configurados en vez de clavar un 7
    dias_alerta = obtener_dias_mantenimiento()
    hoy = datetime.now().date()
    limite_alerta = hoy + timedelta(days=dias_alerta)
    
    maquinas = Maquina.query.all()
    for m in maquinas:
        m.dias_restantes = (m.fecha_proxima_revision - hoy).days
        m.en_alerta = m.fecha_proxima_revision <= limite_alerta
        
    # Ordenamos: Las más urgentes arriba
    maquinas.sort(key=lambda x: x.dias_restantes)
    
    # 🔥 Le pasamos dias_alerta al HTML para el cartel
    return render_template('mantenimiento.html', maquinas=maquinas, hoy=hoy, dias_alerta=dias_alerta)

@app.route('/mantenimiento/nueva', methods=['POST'])
@login_required
def nueva_maquina():
    if current_user.rol not in ['admin', 'jefe_logistica','supervisor']:
        return redirect(url_for('logistica'))

    nombre = request.form.get('nombre').strip().upper()
    descripcion = request.form.get('descripcion').strip()
    fecha_str = request.form.get('proxima_revision')
    
    try:
        fecha_proxima = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        nueva = Maquina(nombre=nombre, descripcion=descripcion, fecha_proxima_revision=fecha_proxima)
        db.session.add(nueva)
        db.session.commit()
        flash(f"🚜 Máquina '{nombre}' registrada al plan de mantenimiento.", "success")
    except Exception as e:
        flash(f"❌ Error al registrar: {str(e)}", "error")
        
    return redirect(url_for('mantenimiento'))

@app.route('/mantenimiento/completar/<int:id>', methods=['POST'])
@login_required
def completar_mantenimiento(id):
    if current_user.rol not in ['admin', 'jefe_logistica']:
        return redirect(url_for('logistica'))

    maquina = Maquina.query.get_or_404(id)
    nueva_fecha_str = request.form.get('nueva_fecha')
    foto = request.files.get('foto_comprobante') # 🔥 Atrapamos la foto
    
    try:
        # 1. Guardar la foto si existe
        if foto and foto.filename != '':
            nombre_archivo = secure_filename(f"tpm_{maquina.id}_{datetime.now().strftime('%Y%m%d')}_{foto.filename}")
            ruta = os.path.join(app.config['CARPETA_MANTENIMIENTO'], nombre_archivo)
            foto.save(ruta)
            maquina.ultimo_comprobante = nombre_archivo

        # 2. Actualizar fechas
        maquina.ultima_revision = datetime.now().date()
        maquina.fecha_proxima_revision = datetime.strptime(nueva_fecha_str, '%Y-%m-%d').date()
        
        db.session.commit()
        flash(f"✅ Mantenimiento de '{maquina.nombre}' registrado con comprobante.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al guardar: {str(e)}", "error")
        
    return redirect(url_for('mantenimiento'))

@app.route('/mantenimiento/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_maquina(id):
    # Seguridad: Solo los jefes pueden dar de baja un equipo
    if current_user.rol not in ['admin', 'jefe_logistica', 'supervisor']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('logistica'))
        
    maquina = Maquina.query.get_or_404(id)
    nombre_borrado = maquina.nombre
    
    try:
        db.session.delete(maquina)
        db.session.commit()
        flash(f"🗑️ Equipo '{nombre_borrado}' eliminado definitivamente del plan de mantenimiento.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al eliminar: {str(e)}", "error")
        
    return redirect(url_for('mantenimiento'))

# Función para leer los días configurados (por defecto 7)
def obtener_dias_mantenimiento():
    try:
        config = Configuracion.query.filter_by(clave='dias_alerta_mantenimiento').first()
        if not config:
            config = Configuracion(clave='dias_alerta_mantenimiento', valor=7)
            db.session.add(config)
            db.session.commit()
            return 7
        return config.valor
    except:
        return 7

# Ruta para que el botón del HTML guarde la nueva configuración
@app.route('/mantenimiento/configurar', methods=['POST'])
@login_required
def configurar_alerta_mantenimiento():
    if current_user.rol not in ['admin', 'jefe_logistica']:
        flash('No tenés permiso para cambiar esta configuración.', 'error')
        return redirect(request.referrer)

    dias = request.form.get('dias', type=int)
    if dias and dias > 0:
        config = Configuracion.query.filter_by(clave='dias_alerta_mantenimiento').first()
        if not config:
            config = Configuracion(clave='dias_alerta_mantenimiento', valor=dias)
            db.session.add(config)
        else:
            config.valor = dias
        db.session.commit()
        flash(f'✅ Alerta de mantenimiento configurada a {dias} días.', 'success')
    
    return redirect(url_for('mantenimiento'))

@app.route('/cambiar_estado_calidad/<int:item_id>', methods=['POST'])
@login_required
def cambiar_estado_calidad(item_id):
    # 1. 🛡️ Seguridad: Solo la élite puede auditar y corregir estados
    roles_permitidos = ['admin', 'jefe_logistica', 'auditoria', 'jefe_auditoria']
    
    if current_user.rol not in roles_permitidos:
        flash("🚫 Acceso denegado: Solo el equipo de Auditoría o Jefatura puede modificar el estado de la mercadería.", "error")
        return redirect(request.referrer)

    item = Item.query.get_or_404(item_id)
    nuevo_estado = request.form.get('nuevo_estado', '').strip().lower()
    
    # Si no mandan nada o el estado es exactamente el mismo que ya tenía, no hacemos nada
    if not nuevo_estado or nuevo_estado == item.estado_calidad:
        return redirect(request.referrer)

    estado_viejo = item.estado_calidad
    item.estado_calidad = nuevo_estado

    # 2. 📝 Registro en el Historial (La Huella Digital)
    ubi_txt = f"{item.ubicacion.codigo_unico.split('-ID')[0]} [Caja: {item.sub_ubicacion}]" if item.sub_ubicacion not in ['General', 'vacia', None] else item.ubicacion.codigo_unico.split('-ID')[0]
    
    nuevo_log = Movimiento(
        tipo='movimiento', # 🔥 FIX: Antes decía 'ajuste', ahora dice 'movimiento' para que salga en la pestaña de Movimientos Internos
        sku=item.producto_detalle.sku,
        cantidad=item.cantidad,
        origen=ubi_txt,
        # Dejamos asentado de qué estado a qué estado lo pasó
        transporte=f"AUDITORÍA: Cambio de calidad ({estado_viejo.upper()} ➔ {nuevo_estado.upper()})",
        usuario=current_user.username,
        sector=item.ubicacion.rack.sector
    )
    
    db.session.add(nuevo_log)
    db.session.commit()

    flash(f"✅ Estado de {item.producto_detalle.sku} actualizado a {nuevo_estado.upper()} y registrado en auditoría.", "success")
    return redirect(request.referrer)

@app.route('/planificacion/procesar_masiva', methods=['POST'])
@login_required
def procesar_planificacion_masiva():
    if current_user.rol not in ['admin', 'planificacion', 'jefe_produccion', 'gerencia']:
        flash('🚫 Acceso denegado.', 'error')
        return redirect(url_for('home'))

    destino = request.form.get('destino_produccion')
    skus_seleccionados = request.form.getlist('skus_seleccionados')
    
    # 🔥 CAPTURA DE FECHA IDÉNTICA A VENTAS
    fecha_str = request.form.get('fecha_planificada_masiva')
    fecha_plan = None
    if fecha_str:
        try:
            fecha_plan = datetime.strptime(fecha_str, '%Y-%m-%d')
        except:
            fecha_plan = datetime.now()
    else:
        fecha_plan = datetime.now()
    
    if not skus_seleccionados:
        flash("⚠️ No seleccionaste ningún producto para planificar.", "error")
        return redirect(url_for('planificacion'))

    # Armamos el paquete de datos
    items_a_procesar = []
    for sku in skus_seleccionados:
        cantidad_raw = request.form.get(f'cant_{sku}', 0)
        try:
            cantidad = int(cantidad_raw)
            if cantidad > 0:
                items_a_procesar.append({'sku': sku, 'cantidad': cantidad})
        except:
            pass

    if not items_a_procesar:
        flash("⚠️ Seleccionaste productos pero las cantidades son 0 o inválidas.", "error")
        return redirect(url_for('planificacion'))

    # ENVIAR A PLANTA LOCAL
    if destino == 'local':
        for item in items_a_procesar:
            prod_db = Producto.query.filter_by(sku=item['sku'], sector='logistica').first()
            desc = prod_db.descripcion if prod_db else "S/D"
            
            nueva_orden = OrdenProduccion(
                sku=item['sku'],
                cantidad=item['cantidad'],
                lote_referencia="Planificación",
                descripcion=desc,
                origen_pedido='Planificación',
                fecha_planificada=fecha_plan # Se inyecta la fecha corregida
            )
            db.session.add(nueva_orden)
            
        db.session.commit()
        fecha_texto = fecha_plan.strftime('%d/%m/%Y')
        flash(f"🏭 ¡Excelente! Se enviaron {len(items_a_procesar)} órdenes a la Planta Local para el {fecha_texto}.", "success")
        return redirect(url_for('planificacion'))

    # ENVIAR A PLANTA EXTERNA (Se mantiene igual)
    elif destino == 'externa':
        import pandas as pd
        import io
        
        data_excel = []
        for item in items_a_procesar:
            prod_db = Producto.query.filter_by(sku=item['sku'], sector='logistica').first()
            desc = prod_db.descripcion if prod_db else "S/D"
            data_excel.append({
                'SKU': item['sku'],
                'Descripción': desc,
                'Cantidad a Fabricar': item['cantidad'],
                'Planta Destino': 'EXTERNA'
            })
        
        df = pd.DataFrame(data_excel)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Orden_Planta_Externa')
            
            worksheet = writer.sheets['Orden_Planta_Externa']
            for col in worksheet.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length: max_length = len(cell.value)
                    except: pass
                worksheet.column_dimensions[column].width = min(max_length + 2, 60)
        
        output.seek(0)
        fecha_hoy = datetime.now().strftime("%d-%m-%Y")
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'Orden_Produccion_Externa_{fecha_hoy}.xlsx'
        )
    
@app.route('/api/busqueda_ventas_vivo')
@login_required
def api_busqueda_ventas_vivo():
    q_sku = request.args.get('q_sku', '').strip().upper()
    q_desc = request.args.get('q_desc', '').strip().upper()
    q_fecha_desde = request.args.get('q_fecha_desde', '').strip()
    q_fecha_hasta = request.args.get('q_fecha_hasta', '').strip()
    anio_filtro = request.args.get('anio_filtro', 0, type=int)
    page = request.args.get('page', 1, type=int)

    query_mov = DetalleVenta.query.join(RegistroVenta)

    # Si anio_filtro es distinto de 0, filtramos por ese año
    if anio_filtro != 0:
        inicio_anio = datetime(anio_filtro, 1, 1).date()
        fin_anio = datetime(anio_filtro, 12, 31).date()
        query_mov = query_mov.filter(RegistroVenta.fecha_venta >= inicio_anio, RegistroVenta.fecha_venta <= fin_anio)

    if q_sku: query_mov = query_mov.filter(DetalleVenta.sku.ilike(f"%{q_sku}%"))
    if q_desc: query_mov = query_mov.filter(DetalleVenta.descripcion.ilike(f"%{q_desc}%"))
    if q_fecha_desde: query_mov = query_mov.filter(func.date(RegistroVenta.fecha_venta) >= q_fecha_desde)
    if q_fecha_hasta: query_mov = query_mov.filter(func.date(RegistroVenta.fecha_venta) <= q_fecha_hasta)

    movimientos = query_mov.order_by(RegistroVenta.fecha_venta.desc(), RegistroVenta.id.desc()).paginate(page=page, per_page=20, error_out=False)

    html_rows = ""
    for item in movimientos.items:
        fecha = item.venta.fecha_venta.strftime('%d/%m/%Y')
        html_rows += f"""
        <tr>
            <td style="color: #64748b; font-size: 13px;">{fecha}</td>
            <td><span style="background: #e0f2fe; color: #0369a1; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">{item.venta.canal}</span></td>
            <td style="font-weight: 800; color: #1e293b;">{item.sku}</td>
            <td style="font-size: 13px; color: #475569;">{item.descripcion}</td>
            <td style="text-align: center;"><span style="background: #f1f5f9; padding: 4px 10px; border-radius: 6px; font-weight: 900;">{item.cantidad}</span></td>
        </tr>
        """
    
    if not movimientos.items:
        html_rows = '<tr><td colspan="5" style="text-align: center; padding: 30px; color: #94a3b8;">No se encontraron ventas para esta búsqueda.</td></tr>'

    return jsonify({
        'html': html_rows,
        'has_next': movimientos.has_next,
        'has_prev': movimientos.has_prev,
        'current_page': movimientos.page,
        'total_pages': movimientos.pages
    })

def calcular_tiempo_neto(inicio, fin, almuerzo_ini_str, almuerzo_fin_str):
    """
    Resta el tiempo de almuerzo si el rango de trabajo lo comprende.
    """
    if not inicio or not fin:
        return 0
        
    formato = "%H:%M"
    # Convertimos strings de config a objetos time
    alm_ini = datetime.strptime(almuerzo_ini_str, formato).time()
    alm_fin = datetime.strptime(almuerzo_fin_str, formato).time()
    
    total_segundos = (fin - inicio).total_seconds()
    
    # Creamos objetos datetime para el almuerzo el mismo día que el inicio
    dt_alm_ini = datetime.combine(inicio.date(), alm_ini)
    dt_alm_fin = datetime.combine(inicio.date(), alm_fin)
    
    # Lógica de solapamiento
    # Si el trabajo empezó antes del fin del almuerzo Y terminó después del inicio del almuerzo
    if inicio < dt_alm_fin and fin > dt_alm_ini:
        # Calculamos cuánto del almuerzo cae dentro del turno
        traslape_inicio = max(inicio, dt_alm_ini)
        traslape_fin = min(fin, dt_alm_fin)
        duracion_almuerzo_en_turno = (traslape_fin - traslape_inicio).total_seconds()
        
        if duracion_almuerzo_en_turno > 0:
            total_segundos -= duracion_almuerzo_en_turno
            
    return max(0, total_segundos)

@app.route('/configurar_almuerzo', methods=['POST'])
@login_required
def configurar_almuerzo():
    if current_user.rol not in ['admin', 'jefe_produccion', 'supervisor_produccion', 'supervisor_produccio', 'encargado']:
        return {"error": "No autorizado"}, 403
        
    inicio_alm = request.form.get('inicio')
    fin_alm = request.form.get('fin')
    inicio_des = request.form.get('desayuno_inicio', '09:00')
    fin_des = request.form.get('desayuno_fin', '09:30')
    
    # 🔥 ATRAPAMOS EL SKU MAESTRO 🔥
    sku_maestro = request.form.get('sku_maestro_a_medida', 'CORT9999').strip().upper()
    
    config = ConfiguracionProduccion.query.first()
    if not config:
        config = ConfiguracionProduccion(
            almuerzo_inicio=inicio_alm, almuerzo_fin=fin_alm, 
            desayuno_inicio=inicio_des, desayuno_fin=fin_des,
            sku_maestro_a_medida=sku_maestro
        )
        db.session.add(config)
    else:
        config.almuerzo_inicio = inicio_alm
        config.almuerzo_fin = fin_alm
        config.desayuno_inicio = inicio_des
        config.desayuno_fin = fin_des
        config.sku_maestro_a_medida = sku_maestro # 🔥 Lo actualizamos
    
    db.session.commit()
    flash("Configuración de Producción actualizada", "success")
    return redirect(url_for('produccion'))


@app.route('/carga_pedidos', methods=['GET', 'POST'])
@login_required
def carga_pedidos():
    # 🔥 Agregamos el rol 'vendedor' (Solo pueden cargar, no ven el tablero financiero)
    if current_user.rol not in ['admin', 'comercial', 'jefe_ventas', 'admin_ventas', 'vendedor']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('home'))

    if request.method == 'POST':
        cliente = request.form.get('cliente')
        cantidad = request.form.get('cantidad', type=int)
        destino = request.form.get('destino_pedido') # 'produccion' o 'logistica'
        
        # --- LÓGICA PARA PRODUCCIÓN ---
        if destino == 'produccion':
            tipo_pedido = request.form.get('tipo_pedido') 
            if tipo_pedido == 'estandar':
                sku = request.form.get('sku').upper()
                prod = Producto.query.filter_by(sku=sku, sector='logistica').first()
                descripcion = prod.descripcion if prod else 'Producto Estándar'
                es_a_medida = False
            else:
                sku = "A MEDIDA"
                es_a_medida = True
                
                t_cortina = request.form.get('med_tipo', '')
                ancho = request.form.get('med_ancho', '')
                alto = request.form.get('med_alto', '')
                lona = request.form.get('med_lona', '')
                
                # 🔥 NUEVOS CAMPOS ATRAPADOS 🔥
                lona2 = request.form.get('med_lona2', '')
                sistema = request.form.get('med_sistema', '')
                comando = request.form.get('med_comando', '')
                cadena = request.form.get('med_cadena', '')
                
                # 🔥 NUEVO: Atrapamos el campo del Peso 🔥
                peso_cadena = request.form.get('med_peso', 'No') 
                
                obs_medida = request.form.get('med_obs', '').strip()
                
                telas_texto = f"{lona} y {lona2}" if (lona2 and 'Doble' in t_cortina) else lona
                
                # 🔥 Agregamos "Peso: Si/No" a la descripción oficial para que el despiece lo lea
                descripcion = f"{t_cortina} | {ancho}x{alto}cm | Telas: {telas_texto} | Sis: {sistema} | Cadena: {cadena} | Peso: {peso_cadena} | Com: {comando}"
                
                if obs_medida:
                    descripcion += f" | Obs: {obs_medida}"
            
            nuevo_pedido = PedidoCliente(
                cliente=cliente, es_a_medida=es_a_medida, sku=sku,
                descripcion=descripcion, cantidad=cantidad,
                vendedor=current_user.username, estado='Pendiente'
            )
            db.session.add(nuevo_pedido)

        # --- LÓGICA PARA LOGÍSTICA (VENTA DIRECTA DE STOCK) ---
        else:
            sku = request.form.get('sku_logistica').upper()
            prod = Producto.query.filter_by(sku=sku, sector='logistica').first()
            
            if not prod:
                flash(f"❌ Error: El SKU {sku} no existe en el catálogo de Logística.", "error")
                return redirect(url_for('carga_pedidos'))

            nuevo_pedido = PedidoCliente(
                cliente=cliente, es_a_medida=False, sku=sku,
                descripcion=prod.descripcion, cantidad=cantidad,
                vendedor=current_user.username, estado='En Logística'
            )
            db.session.add(nuevo_pedido)
            db.session.flush()

            nro_vta = datetime.now().strftime('%H%M')
            nueva_tarea = TareaPicking(
                fecha=datetime.now().strftime('%Y-%m-%d'),
                zona=f"Venta Directa - {cliente}",
                producto=prod.descripcion,
                sku=sku,
                descripcion=f"Pedido #{nuevo_pedido.id} - {cliente}",
                cantidad=cantidad,
                estado='Pendiente'
            )
            db.session.add(nueva_tarea)

        # ====================================================================
        # 🔥 LA MAGIA ESTÁ ACÁ: INYECCIÓN AL MÓDULO DE ANÁLISIS DE VENTAS 🔥
        # ====================================================================
        db.session.flush() # Obligamos a la base de datos a darnos el ID del nuevo_pedido
        
        # Le inventamos un número de comprobante único para que quede prolijo
        comprobante_automatico = f"MANUAL-{nuevo_pedido.id}-{hora_argentina().strftime('%d%m')}"
        
        nueva_venta = RegistroVenta(
            nro_comprobante=comprobante_automatico,
            fecha_venta=hora_argentina().date(), # Toma la fecha exacta de HOY (Paraguay)
            cliente=cliente,
            canal="Venta", # Para que en los gráficos lo puedas distinguir de MercadoLibre
            total_venta=0.0
        )
        db.session.add(nueva_venta)
        db.session.flush()
        
        detalle_venta = DetalleVenta(
            venta_id=nueva_venta.id,
            sku=nuevo_pedido.sku,
            descripcion=nuevo_pedido.descripcion,
            cantidad=nuevo_pedido.cantidad,
            precio_unitario=0.0,
            subtotal=0.0
        )
        db.session.add(detalle_venta)
        # ====================================================================

        db.session.commit()
        flash(f"✅ Pedido para {destino.upper()} cargado con éxito y registrado en Análisis de Ventas.", "success")
        return redirect(url_for('carga_pedidos'))
        
    pedidos_recientes = PedidoCliente.query.order_by(PedidoCliente.id.desc()).limit(15).all()
    return render_template('carga_pedidos.html', pedidos=pedidos_recientes)


@app.route('/planificar_pedido_cliente/<int:pedido_id>', methods=['POST'])
@login_required
def planificar_pedido_cliente(pedido_id):
    pedido = PedidoCliente.query.get_or_404(pedido_id)
    
    fecha_str = request.form.get('fecha_planificada')
    if fecha_str:
        fecha_plan = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    else:
        fecha_plan = datetime.now().date()
        
    prioridad_elegida = request.form.get('prioridad', 'Normal')
    
    nueva_orden = OrdenProduccion(
        sku=pedido.sku,
        descripcion=f"[Cliente: {pedido.cliente}] {pedido.descripcion}",
        cantidad=pedido.cantidad,
        estado='Pendiente',
        prioridad=prioridad_elegida, 
        origen_pedido='Ventas',
        lote_referencia=f"PED-{pedido.id}",
        fecha_planificada=fecha_plan
    )
    db.session.add(nueva_orden)
    pedido.estado = 'Planificado' 
    db.session.commit()
    
    # 🔥 EL FIX MAGISTRAL: Respondemos de forma "invisible"
    return jsonify({
        'status': 'success',
        'mensaje': f'Pedido de {pedido.cliente} programado.'
    })

@app.route('/admin/vaciar_pedidos_cargados', methods=['GET', 'POST'])
@login_required
def vaciar_pedidos_cargados():
    # 🔒 Solo Admin
    if current_user.rol != 'admin':
        flash("🚫 No tenés permisos para esta acción.", "error")
        return redirect(url_for('home'))

    try:
        # Vaciamos la tabla de los pedidos que cargan los vendedores
        db.session.query(PedidoCliente).delete()
        db.session.commit()
        flash("✅ Historial de pedidos cargados vaciado correctamente.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al vaciar: {str(e)}", "error")
        
    return redirect(url_for('carga_pedidos'))

@app.route('/api/buscar_sku_ventas')
@login_required
def buscar_sku_ventas():
    q = request.args.get('q', '').strip().upper()
    if len(q) < 2: 
        return jsonify([])

    # Buscamos en el catálogo de logística (fuente maestra)
    # Filtramos por SKU o Descripción y limitamos a 20 para máxima velocidad
    productos = Producto.query.filter(
        Producto.sector == 'logistica',
        db.or_(Producto.sku.ilike(f"%{q}%"), Producto.descripcion.ilike(f"%{q}%"))
    ).limit(20).all()

    return jsonify([{'sku': p.sku, 'descripcion': p.descripcion} for p in productos])

@app.route('/produccion/borrar_registro/<int:id>', methods=['POST'])
@login_required
def borrar_registro_produccion(id):
    # 🔥 PATOVICA: Bloqueamos a los supervisores/encargados
    if current_user.rol in ['supervisor', 'supervisor_produccion', 'supervisor_produccio', 'encargado']:
        flash("🚫 No tienes permisos para eliminar registros del historial.", "error")
        return redirect(url_for('historial_produccion'))

    # Buscamos la orden y la borramos
    orden = OrdenProduccion.query.get_or_404(id)
    
    try:
        db.session.delete(orden)
        db.session.commit()
        flash("🗑️ Registro de producción eliminado del historial.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al eliminar: {str(e)}", "error")

    return redirect(url_for('historial_produccion'))

@app.route('/api/stock_para_sheets')
def stock_para_sheets():
    # 🛡️ Token de seguridad: Cambiá 'MES2026' por lo que quieras
    token = request.args.get('token')
    if token != "MES2026":
        return "No autorizado", 403

    # 1. Traemos TODO el catálogo de Logística que empiece con "CORT"
    productos_cort = Producto.query.filter(
        Producto.sector == 'logistica',
        Producto.sku.like('CORT%')
    ).order_by(Producto.sku.asc()).all()

    # 2. Buscamos dónde hay cajas físicas en los estantes de Logística
    stock_raw = db.session.query(
        Producto.sku, 
        db.func.sum(Item.cantidad)
    ).join(Item, Producto.id == Item.producto_id)\
     .join(Ubicacion, Item.ubicacion_id == Ubicacion.id)\
     .join(Rack, Ubicacion.rack_id == Rack.id)\
     .filter(Rack.sector == 'logistica', Item.cantidad > 0)\
     .group_by(Producto.sku).all()

    # Convertimos el resultado en un diccionario súper rápido: {'CORT0001': 15, 'CORT0005': 2}
    dict_stock = {s[0]: s[1] for s in stock_raw}

    # 3. Armamos la planilla CSV en la memoria
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['SKU', 'Descripcion', 'Stock Actual', 'Ultima Actualizacion'])
    
    ahora = hora_argentina().strftime('%d/%m/%Y %H:%M')
    
    # 4. Cruzamos el catálogo con el stock. Si no está en el diccionario, es 0.
    for p in productos_cort:
        stock_actual = dict_stock.get(p.sku, 0)
        cw.writerow([p.sku, p.descripcion, stock_actual, ahora])

    # Empaquetamos y enviamos a Google Sheets
    # Usamos utf-8-sig para que la Ñ y los acentos se lean perfecto
    output = make_response(si.getvalue().encode('utf-8-sig'))
    output.headers["Content-Disposition"] = "attachment; filename=stock_mes.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output

@app.route('/exportar_busqueda_logistica')
@login_required
def exportar_busqueda_logistica():
    if current_user.rol == 'operario':
        flash("🚫 Acceso denegado: Tu perfil no tiene permisos para exportar.", "error")
        return redirect(url_for('home'))

    # Atrapamos los mismos parámetros que usó para buscar
    termino = request.args.get('q', '').strip().upper()
    sector_actual = request.args.get('sector', 'logistica') 
    f_sku = request.args.get('f_sku', '').strip().upper()
    f_desc = request.args.get('f_desc', '').strip().upper()
    f_estado = request.args.get('f_estado', '').strip().lower()

    if not termino:
        flash("⚠️ No hay ningún filtro aplicado para exportar.", "error")
        return redirect(request.referrer or url_for('logistica'))
    
    # 1. PRIMERO ARMAMOS LA BÚSQUEDA BASE
    query = Item.query.join(Producto).join(Ubicacion).join(Rack).filter(Rack.sector == sector_actual)
    
    # 2. DESPUÉS LE APLICAMOS TODOS LOS FILTROS
    query = query.filter(db.or_(
        Producto.sku.ilike(f"{termino}%"),
        Producto.descripcion.ilike(f"%{termino}%"),
        Item.lote.ilike(f"{termino}%")
    ))
    
    if f_sku:
        query = query.filter(Producto.sku.ilike(f"%{f_sku}%"))
    if f_desc:
        query = query.filter(Producto.descripcion.ilike(f"%{f_desc}%"))
    if f_estado:
        query = query.filter(Item.estado_calidad == f_estado) # 🔥 AHORA SÍ ESTÁ EN EL ORDEN CORRECTO

    # Traemos TODOS los resultados juntos (sin paginación)
    resultados = query.order_by(Producto.sku.asc()).all()

    if not resultados:
        flash("No hay resultados para exportar.", "info")
        return redirect(request.referrer)

    # Convertimos a datos para Excel
    data = []
    for i in resultados:
        data.append({
            'SKU': i.producto_detalle.sku,
            'Descripción': i.producto_detalle.descripcion,
            'Lote / Partida': i.lote or '-',
            'Vencimiento': i.fecha_vencimiento or '-',
            'Cantidad': i.cantidad,
            'Estante Físico': i.ubicacion.codigo_unico.split('-ID')[0],
            'Sub-Ubicación / Caja': i.sub_ubicacion or 'General',
            'Estado': i.estado_calidad.upper()
        })

    # Armamos el Excel
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Resultados Busqueda')
        
        # Ajustar ancho de columnas
        worksheet = writer.sheets['Resultados Busqueda']
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length: max_length = len(cell.value)
                except: pass
            worksheet.column_dimensions[column].width = min(max_length + 2, 60)

    output.seek(0)
    fecha_hoy = datetime.now().strftime("%d-%m-%Y_%H%M")

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Filtro_{sector_actual.upper()}_{termino}_{fecha_hoy}.xlsx'
    )

@app.route('/exportar_planificacion')
@login_required
def exportar_planificacion():
    # Mismos roles que pueden entrar a Planificación
    roles_permitidos = ['admin', 'planificacion', 'jefe_produccion', 'gerencia', 'analisis_ventas', 'administrativo', 'comercial', 'jefe_ventas', 'admin_ventas']
    if current_user.rol not in roles_permitidos:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('home'))

    cobertura_dias = request.args.get('cobertura_dias', 30, type=int)
    q = request.args.get('q', '').strip().upper() 

    # Búsqueda en la Base de Datos
    query_prod = Producto.query.filter(Producto.sector == 'logistica', Producto.sku != 'SUBDIVISION_VACIA')
    if q:
        query_prod = query_prod.filter(db.or_(Producto.sku.ilike(f"%{q}%"), Producto.descripcion.ilike(f"%{q}%")))
    catalogo = query_prod.all()

    # Recopilar la matemática
    fecha_hace_90 = datetime.now().date() - timedelta(days=90)
    ventas_90_raw = db.session.query(DetalleVenta.sku, func.sum(DetalleVenta.cantidad)).join(RegistroVenta).filter(func.date(RegistroVenta.fecha_venta) >= fecha_hace_90).group_by(DetalleVenta.sku).all()
    dict_ventas = {v[0]: v[1] for v in ventas_90_raw}

    stock_raw = db.session.query(Producto.sku, func.sum(Item.cantidad)).join(Item).join(Ubicacion).join(Rack).filter(Rack.sector == 'logistica', Item.cantidad > 0).group_by(Producto.sku).all()
    dict_stock = {s[0]: s[1] for s in stock_raw}

    wip_raw = db.session.query(OrdenProduccion.sku, func.sum(OrdenProduccion.cantidad)).filter(OrdenProduccion.estado.in_(['Pendiente', 'En Proceso'])).group_by(OrdenProduccion.sku).all()
    dict_wip = {w[0]: w[1] for w in wip_raw}

    import math
    hoy_obj = hora_argentina().date()
    data_excel = []

    for prod in catalogo:
        ventas_90 = float(dict_ventas.get(prod.sku, 0))
        promedio_diario = ventas_90 / 90.0
        promedio_mensual = int(round(promedio_diario * 30))
        demanda_proyectada = promedio_diario * cobertura_dias
        stock = int(dict_stock.get(prod.sku, 0))
        wip = int(dict_wip.get(prod.sku, 0))
        
        sugerido = int(math.ceil(demanda_proyectada) - stock - wip)
        if sugerido < 0: sugerido = 0
        
        if promedio_diario > 0:
            dias_stock = int((stock + wip) / promedio_diario)
            fecha_quiebre_obj = hoy_obj + timedelta(days=dias_stock)
            fecha_quiebre = fecha_quiebre_obj.strftime('%d/%m/%Y')
            dias_texto = f"{dias_stock} días" if dias_stock > 0 else "0 días (HOY)"
        else:
            fecha_quiebre = "∞"
            dias_texto = "Sin salida"

        estado = "Falta Fabricar" if sugerido > 0 else ("Stock Justo" if (stock + wip) <= (demanda_proyectada * 1.2) else "Stock Óptimo")

        data_excel.append({
            'SKU': prod.sku,
            'Descripción': prod.descripcion,
            'Promedio Venta Mensual': promedio_mensual,
            'Stock Físico Logística': stock,
            'En Fábrica (WIP)': wip,
            'Estado': estado,
            'Fecha Quiebre Stock': fecha_quiebre,
            'Días Restantes': dias_texto,
            'A Fabricar (Sugerido)': sugerido
        })

    # Ordenar poniendo arriba lo que más urge fabricar
    data_excel.sort(key=lambda x: x['A Fabricar (Sugerido)'], reverse=True)

    # Armar archivo Excel
    import pandas as pd
    import io
    df = pd.DataFrame(data_excel)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Planificacion_MRP')
        
        # Ajuste automático del ancho de las columnas
        worksheet = writer.sheets['Planificacion_MRP']
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length: max_length = len(cell.value)
                except: pass
            worksheet.column_dimensions[column].width = min(max_length + 2, 60)

    output.seek(0)
    fecha_hoy = hoy_obj.strftime("%d-%m-%Y")
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'MRP_Filtrado_{fecha_hoy}.xlsx'
    )

@app.route('/pausar_produccion/<int:orden_id>', methods=['POST'])
@login_required
def pausar_produccion(orden_id):
    # 🔥 ESCUDO: Solo Jefes, Supervisores y Admin pueden pausar/deshacer
    roles_permitidos = ['admin', 'supervisor_produccion', 'supervisor_produccio', 'jefe_produccion', 'supervisor', 'encargado']
    if current_user.rol not in roles_permitidos:
        flash("🚫 No tienes permisos para pausar órdenes.", "error")
        return redirect(request.referrer)

    orden = OrdenProduccion.query.get_or_404(orden_id)
    motivo = request.form.get('motivo_pausa', '').strip()

    if orden.estado == 'En Proceso':
        # Volvemos a ponerlo en cola y apagamos el reloj
        orden.estado = 'Pendiente'
        orden.fecha_inicio = None 
        orden.operario_inicio = None
        
        # Dejamos la huella en la descripción
        texto_pausa = f" [⏸️ PAUSADO/RESETEO por {current_user.username}: {motivo}]"
        orden.descripcion = (orden.descripcion or "") + texto_pausa
        
        db.session.commit()
        
    return redirect(request.referrer)

@app.route('/produccion/enviar_logistica/<int:orden_id>', methods=['POST'])
@login_required
def enviar_logistica_planificada(orden_id):
    if current_user.rol not in ['admin', 'supervisor_produccion', 'supervisor_produccio', 'operario_produccion', 'produccion', 'jefe_produccion', 'planificacion', 'encargado']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(request.referrer)

    orden = OrdenProduccion.query.get_or_404(orden_id)
    
    # 1. Generamos el Número de Remito
    ultimo_id = Transferencia.query.order_by(Transferencia.id.desc()).first()
    next_id = (ultimo_id.id + 1) if ultimo_id else 1
    nro_remito = f"R-26-{next_id:04d}"

    # 2. Creamos la transferencia (Viaja a la recepción de Logística)
    nueva_transf = Transferencia(
        remito_nro=nro_remito,
        sku=orden.sku,
        descripcion=orden.descripcion,
        cantidad=orden.cantidad,
        estado_calidad='apto', # Sale de fábrica 100% apto
        usuario_envia=current_user.username
    )
    db.session.add(nueva_transf)

    # 3. Sacamos la tarjeta del tablero de Producción
    orden.estado = 'Entregado'
    
    # 4. Dejamos huella en el historial
    log_mov = Movimiento(
        tipo='movimiento',
        sku=orden.sku,
        cantidad=orden.cantidad,
        origen="FÁBRICA (Producción)",
        transporte=f"REMITO A LOGÍSTICA: {nro_remito}",
        usuario=current_user.username,
        sector='produccion'
    )
    db.session.add(log_mov)
    db.session.commit()
    
    flash(f"📦 Remito {nro_remito} generado con éxito.", "success")
    
    # Abre el PDF del remito
    return redirect(url_for('ver_remito', trans_id=nueva_transf.id))

@app.route('/produccion/enviar_logistica_masiva', methods=['POST'])
@login_required
def enviar_logistica_masiva():
    if current_user.rol not in ['admin', 'supervisor_produccion', 'supervisor_produccio', 'operario_produccion', 'produccion', 'jefe_produccion', 'planificacion', 'encargado']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(request.referrer)

    ordenes_ids = request.form.getlist('ordenes_seleccionadas')
    if not ordenes_ids:
        flash("⚠️ No seleccionaste ninguna orden para el remito.", "warning")
        return redirect(request.referrer)

    # 1. Generamos UN SOLO número base de remito
    ultimo_id = Transferencia.query.order_by(Transferencia.id.desc()).first()
    next_id = (ultimo_id.id + 1) if ultimo_id else 1
    nro_remito_base = f"R-26-{next_id:04d}"

    # 2. Procesamos todas las órdenes tildadas
    for indice, oid in enumerate(ordenes_ids, start=1):
        orden = OrdenProduccion.query.get(oid)
        if orden and orden.estado == 'Finalizado':
            
            # 🔥 FIX: Le agregamos un guión y el número de línea (-1, -2, -3) 
            # para que la base de datos no rebote por la regla de "Único"
            nro_remito_linea = f"{nro_remito_base}-{indice}"

            # Se la mandamos a Logística
            nueva_transf = Transferencia(
                remito_nro=nro_remito_linea,
                sku=orden.sku,
                descripcion=orden.descripcion,
                cantidad=orden.cantidad,
                estado_calidad='apto',
                usuario_envia=current_user.username
            )
            db.session.add(nueva_transf)

            # Ocultamos la tarjeta del tablero
            orden.estado = 'Entregado'
            
            # Historial de fábrica
            log_mov = Movimiento(
                tipo='movimiento',
                sku=orden.sku,
                cantidad=orden.cantidad,
                origen="FÁBRICA (Producción)",
                transporte=f"REMITO A LOGÍSTICA: {nro_remito_base}",
                usuario=current_user.username,
                sector='produccion'
            )
            db.session.add(log_mov)

    db.session.commit()
    flash(f"📦 Remito Consolidado {nro_remito_base} generado con éxito.", "success")
    return redirect(url_for('ver_remito_consolidado', nro_remito=nro_remito_base))


@app.route('/ver_remito_consolidado/<nro_remito>')
@login_required
def ver_remito_consolidado(nro_remito):
    # 🔥 FIX: Buscamos todas las líneas que EMPIECEN con el número base de remito
    transferencias = Transferencia.query.filter(Transferencia.remito_nro.like(f"{nro_remito}%")).all()
    
    if not transferencias:
        return "Remito no encontrado", 404
    
    fecha = transferencias[0].fecha_envio if transferencias else hora_argentina()
    usuario = transferencias[0].usuario_envia if transferencias else current_user.username

    return render_template('remito_consolidado.html', transferencias=transferencias, nro_remito=nro_remito, fecha=fecha, usuario=usuario)

@app.route('/posventa/reportes')
@login_required
def reportes_posventa():
    # Candado de seguridad
    if current_user.rol not in ['admin', 'jefe_posventa', 'posventa', 'consultas']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('home'))

    # 1. Total Ingresos a Posventa (Buscamos en el historial)
    ingresos_totales = db.session.query(func.sum(Movimiento.cantidad)).filter_by(
        sector='posventa', tipo='ingreso'
    ).scalar() or 0

    # 2. Estadísticas de Reparaciones Finalizadas
    reparaciones = db.session.query(
        Reparacion.resolucion_calidad, func.sum(Reparacion.cantidad)
    ).filter_by(estado='Finalizado').group_by(Reparacion.resolucion_calidad).all()
    
    rep_stats = {'apto': 0, 'outlet': 0, 'desguace': 0, 'no_apto': 0}
    total_reparado = 0
    for res, cant in reparaciones:
        if res and res in rep_stats:
            rep_stats[res] = cant
        total_reparado += cant

    # 3. Despachos a Logística (Remitos de Posventa)
    # Como los remitos de Posventa tienen menos de 3 guiones (Ej: R-26-0010)
    transferencias_todas = Transferencia.query.all()
    enviados_apto = 0
    enviados_outlet = 0
    for t in transferencias_todas:
        if t.remito_nro.count('-') < 3: 
            if t.estado_calidad == 'apto':
                enviados_apto += t.cantidad
            elif t.estado_calidad == 'outlet':
                enviados_outlet += t.cantidad
                
    total_enviado = enviados_apto + enviados_outlet

    # 4. Últimas 10 reparaciones para la tablita rápida
    ultimas_rep = Reparacion.query.filter_by(estado='Finalizado').order_by(Reparacion.fecha_fin.desc()).limit(10).all()

    return render_template(
        'reportes_posventa.html',
        ingresos_totales=ingresos_totales,
        rep_stats=rep_stats,
        total_reparado=total_reparado,
        enviados_apto=enviados_apto,
        enviados_outlet=enviados_outlet,
        total_enviado=total_enviado,
        ultimas_rep=ultimas_rep
    )

@app.route('/limpieza_profunda_posventa')
@login_required
def limpieza_profunda_posventa():
    # 🔒 SUPER SEGURIDAD: Solo el admin puede hacer esto
    if current_user.rol != 'admin':
        flash("🚫 Acceso denegado. Solo el Administrador puede borrar la base de datos.", "error")
        return redirect(url_for('home'))

    try:
        # 1. Borrar todas las Tarjetas de Reparación
        Reparacion.query.delete()

        # 2. Borrar todo el Historial (Movimientos) que sea de Posventa
        Movimiento.query.filter_by(sector='posventa').delete()

        # 3. Borrar los Remitos (Transferencias) que salieron de Posventa
        # Sabemos que los de Posventa tienen menos de 3 guiones (Ej: R-26-0010)
        transferencias = Transferencia.query.all()
        for t in transferencias:
            if t.remito_nro.count('-') < 3:
                db.session.delete(t)

        # 4. (Opcional) Vaciar los estantes físicos de Posventa 
        # Si también querés que el Taller y el depósito de Posventa queden vacíos, descomentá estas 2 líneas de abajo:
        # items_posventa = Item.query.join(Ubicacion).join(Rack).filter(Rack.sector == 'posventa').all()
        # for item in items_posventa: db.session.delete(item)

        db.session.commit()
        flash("🧹 ¡Limpieza exitosa! El historial y las reparaciones de Posventa están en cero.", "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al limpiar la base de datos: {str(e)}", "error")

    return redirect(url_for('home'))

# ==========================================
# CARGA MASIVA DE DEVOLUCIONES (COMERCIAL)
# ==========================================

@app.route('/descargar_plantilla_devoluciones')
@login_required
def descargar_plantilla_devoluciones():
    
    columnas = [
        'Numero Venta', 'Compra Empresa', 'SKU', 'Producto', 'Cantidad', 
        'Fecha Compra', 'Fecha Reclamo', 'Quien Reporta', 'Nombre Cliente', 
        'Lugar Entrega', 'Facturacion', 'Motivo Devolucion', 'Observaciones', 
        'Tipo Gestion', 'Condicion'
    ]
    
    ejemplo = {
        'Numero Venta': '2000145899',
        'Compra Empresa': 'MercadoLibre',
        'SKU': 'DRAX0104',
        'Producto': 'BICI DRAX DRACO R29 M18 VERDE',
        'Cantidad': 1,
        'Fecha Compra': '2026-04-10',
        'Fecha Reclamo': '2026-04-20',
        'Quien Reporta': 'Vendedor 1',
        'Nombre Cliente': 'Juan Perez',
        'Lugar Entrega': 'CABA',
        'Facturacion': 'Factura B',
        'Motivo Devolucion': 'Llegó rayado el cuadro',
        'Observaciones': 'El cliente pide cambio de unidad',
        'Tipo Gestion': 'Cambio Directo',
        'Condicion': 'Nuevo'
    }
    
    df = pd.DataFrame([ejemplo], columns=columnas)
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Plantilla_Devoluciones')
        worksheet = writer.sheets['Plantilla_Devoluciones']
        # Ajuste visual de columnas
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length: max_length = len(cell.value)
                except: pass
            worksheet.column_dimensions[column].width = min(max_length + 2, 40)

    output.seek(0)
    return send_file(
        output, 
        download_name="Plantilla_Carga_Devoluciones.xlsx", 
        as_attachment=True, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/importar_devoluciones_masivas', methods=['POST'])
@login_required
def importar_devoluciones_masivas():
    if current_user.rol not in ['admin', 'comercial', 'gerencia']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(url_for('comercial'))

    archivo = request.files.get('archivo_devoluciones')
    if not archivo or archivo.filename == '':
        flash("❌ No seleccionaste ningún archivo.", "error")
        return redirect(url_for('comercial'))

    try:
        import pandas as pd
        df = pd.read_excel(archivo)
        
        cols = {str(c).strip().lower().replace(' ', '').replace('_', '').replace('ó', 'o'): c for c in df.columns}
        
        def get_val(fila, posibles_nombres, default=""):
            for nombre in posibles_nombres:
                if nombre in cols:
                    val = fila.get(cols[nombre])
                    if pd.notna(val):
                        return str(val).strip()
            return default

        def get_date(fila, posibles_nombres):
            for nombre in posibles_nombres:
                if nombre in cols:
                    val = fila.get(cols[nombre])
                    if pd.notna(val) and str(val).strip() != '':
                        try:
                            if isinstance(val, datetime): return val.date()
                            return pd.to_datetime(val, dayfirst=True).date()
                        except: pass
            return None

        agregados = 0

        # =====================================================================
        # 🔥 EL FIX MAGISTRAL: Pre-cargamos el catálogo en la memoria de Python
        # =====================================================================
        productos_db = Producto.query.filter(Producto.sector.in_(['logistica', 'posventa'])).all()
        # Armamos un diccionario súper rápido para buscar: {'DRAX0104': 'BICI DRAX...'}
        diccionario_productos = {p.sku.upper(): p.descripcion for p in productos_db}

        for index, row in df.iterrows():
            nro_venta = get_val(row, ['numeroventa', 'venta', 'nroventa'])
            sku = get_val(row, ['sku', 'codigo']).upper()
            
            if not nro_venta and not sku:
                continue

            # 🔥 LA SOLUCIÓN: Buscamos la descripción usando el diccionario
            desc_excel = get_val(row, ['producto', 'descripcion', 'desc'])
            
            if sku in diccionario_productos:
                descripcion_final = diccionario_productos[sku] # Si lo encuentra, usa la oficial
            elif desc_excel:
                descripcion_final = desc_excel # Si no, usa lo que decía el Excel
            else:
                descripcion_final = "S/D" # Si no hay nada, pone S/D

            try: cantidad = int(float(get_val(row, ['cantidad', 'cant'], 1)))
            except: cantidad = 1

            nueva = IncidenciaComercial(
                numero_venta=nro_venta,
                compra_empresa=get_val(row, ['compraempresa', 'empresa', 'canal']),
                sku=sku,
                producto=descripcion_final, # 👈 ¡Acá inyectamos la descripción oficial!
                cantidad=cantidad,
                fecha_compra=get_date(row, ['fechacompra', 'compra']),
                fecha_reclamo=get_date(row, ['fechareclamo', 'reclamo']),
                quien_reporta=get_val(row, ['quienreporta', 'vendedor']),
                nombre_cliente=get_val(row, ['nombrecliente', 'cliente']),
                lugar_entrega=get_val(row, ['lugarentrega', 'entrega', 'destino']),
                facturacion=get_val(row, ['facturacion', 'factura']),
                motivo_devolucion=get_val(row, ['motivodevolucion', 'motivo']),
                observaciones=get_val(row, ['observaciones', 'obs']),
                tipo_gestion=get_val(row, ['tipogestion', 'gestion']),
                estado='Abierto',
                condicion=get_val(row, ['condicion', 'estado', 'estadoproducto'])
            )
            
            db.session.add(nueva)
            db.session.flush() 
            
            nueva.numero_reclamo = f"TK-{nueva.id:05d}"
            agregados += 1

        db.session.commit()
        flash(f"✅ Se cargaron {agregados} incidencias correctamente desde el Excel.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"❌ Error al leer el Excel: {str(e)}", "error")

    return redirect(url_for('comercial'))

# ==========================================
# REMITO CONSOLIDADO PARA POSVENTA
# ==========================================
@app.route('/posventa/enviar_logistica_masiva', methods=['POST'])
@login_required
def enviar_logistica_masiva_posventa():
    # 🔒 Seguridad
    if current_user.rol not in ['admin', 'posventa', 'jefe_posventa', 'administrativo']:
        flash("🚫 Acceso denegado.", "error")
        return redirect(request.referrer)

    # 1. Atrapamos los IDs de los ítems seleccionados
    items_ids = request.form.getlist('items_seleccionados')
    if not items_ids:
        flash("⚠️ No seleccionaste ningún producto para el remito.", "warning")
        return redirect(request.referrer)

    # 2. Generamos un número base de remito (Igual que el de fábrica)
    ultimo_id = Transferencia.query.order_by(Transferencia.id.desc()).first()
    next_id = (ultimo_id.id + 1) if ultimo_id else 1
    nro_remito_base = f"R-26-{next_id:04d}"

    procesados = 0
    # 3. Procesamos cada ítem tildado
    for indice, iid in enumerate(items_ids, start=1):
        item = Item.query.get(iid)
        if item and item.cantidad > 0:
            # 🛡️ Aduana: ¿Logística tiene este SKU?
            prod_log = Producto.query.filter_by(sku=item.producto_detalle.sku, sector='logistica').first()
            if not prod_log:
                continue # Si no existe en logística, lo salteamos (o podrías dar error)

            # Creamos la línea del remito con sufijo para que no explote la DB
            nro_linea = f"{nro_remito_base}-{indice}"
            
            nueva_transf = Transferencia(
                remito_nro=nro_linea,
                sku=item.producto_detalle.sku,
                descripcion=item.producto_detalle.descripcion,
                cantidad=item.cantidad,
                estado_calidad=item.estado_calidad,
                usuario_envia=current_user.username
            )
            db.session.add(nueva_transf)

            # 📝 Historial de salida de Posventa
            log_mov = Movimiento(
                tipo='movimiento',
                sku=item.producto_detalle.sku,
                cantidad=item.cantidad,
                origen=item.ubicacion.codigo_unico.split('-ID')[0],
                transporte=f"REMITO CONSOLIDADO: {nro_remito_base}",
                usuario=current_user.username,
                sector='posventa'
            )
            db.session.add(log_mov)

            # 🗑️ Borramos el ítem del estante de Posventa
            db.session.delete(item)
            procesados += 1

    if procesados > 0:
        db.session.commit()
        flash(f"📦 Remito {nro_remito_base} generado con {procesados} productos.", "success")
        # Reutilizamos la misma vista de remito consolidado que ya tenés
        return redirect(url_for('ver_remito_consolidado', nro_remito=nro_remito_base))
    else:
        db.session.rollback()
        flash("❌ No se pudo procesar ningún ítem (Verificá que los SKUs existan en Logística).", "error")
        return redirect(request.referrer)


# ==========================================
# HISTORIAL EXCLUSIVO MATERIAS PRIMAS
# ==========================================
@app.route('/historial_materias_primas')
@login_required
def historial_materias_primas():
    # 1. Seguridad estricta
    roles_permitidos = ['admin', 'jefe_materias_primas', 'encargado']
    if current_user.rol.lower() not in roles_permitidos:
        flash("🚫 Acceso denegado al historial de Materias Primas.", "error")
        return redirect(url_for('materias_primas'))

    # 2. Capturamos los filtros de búsqueda
    q_sku = request.args.get('q_sku', '').strip()
    q_operario = request.args.get('q_operario', '').strip()
    q_fecha = request.args.get('q_fecha', '').strip()
    
    # 3. Control de pestañas y páginas
    tab_activa = request.args.get('tab', 'ingresos_tab')
    p_in = request.args.get('p_in', 1, type=int)
    p_mov = request.args.get('p_mov', 1, type=int)
    p_aj = request.args.get('p_aj', 1, type=int)
    LIMITE = 50

    # 4. Motores de filtrado
    def filtrar_movimientos(query_base):
        q = query_base
        if q_sku: q = q.filter(Movimiento.sku.ilike(f"%{q_sku}%"))
        if q_operario: q = q.filter(Movimiento.usuario.ilike(f"%{q_operario}%"))
        if q_fecha: q = q.filter(db.func.date(Movimiento.fecha) == q_fecha)
        return q.order_by(Movimiento.fecha.desc())

    def filtrar_ajustes(query_base):
        q = query_base
        if q_sku: q = q.filter(HistorialAjuste.sku.ilike(f"%{q_sku}%"))
        if q_operario: q = q.filter(HistorialAjuste.usuario.ilike(f"%{q_operario}%"))
        if q_fecha: q = q.filter(db.func.date(HistorialAjuste.fecha) == q_fecha)
        return q.order_by(HistorialAjuste.fecha.desc())

    # 5. Ejecución de consultas (Exclusivo sector 'materias_primas')
    q_ingresos = Movimiento.query.filter_by(tipo='ingreso', sector='materias_primas')
    ingresos = filtrar_movimientos(q_ingresos).paginate(page=p_in, per_page=LIMITE, error_out=False)

    q_movs = Movimiento.query.filter_by(tipo='movimiento', sector='materias_primas')
    movimientos = filtrar_movimientos(q_movs).paginate(page=p_mov, per_page=LIMITE, error_out=False)

    q_ajustes = HistorialAjuste.query.filter_by(sector='materias_primas')
    ajustes = filtrar_ajustes(q_ajustes).paginate(page=p_aj, per_page=LIMITE, error_out=False)

    return render_template('historial_mp.html', 
                           ingresos=ingresos, movimientos=movimientos, ajustes=ajustes, 
                           tab_activa=tab_activa, q_sku=q_sku, q_operario=q_operario, q_fecha=q_fecha)

# 🔥 RUTA OCULTA: VACIAR HISTORIAL DE MATERIAS PRIMAS 🔥
@app.route('/vaciar_historial_mp_secreto')
@login_required
def vaciar_historial_mp_secreto():
    # 🔒 SEGURIDAD: Solo el Admin puede gatillar esto desde la URL
    if current_user.rol.lower() != 'admin':
        return "<h1>🚫 Acceso Denegado</h1><p>No tenés permisos para ejecutar esta acción.</p>", 403

    try:
        # 1. Borramos la tabla de Movimientos (Ingresos y Movimientos Internos) de MP
        Movimiento.query.filter_by(sector='materias_primas').delete()

        # 2. Borramos la tabla de Ajustes de Stock de MP
        HistorialAjuste.query.filter_by(sector='materias_primas').delete()
        
        # 3. Guardamos los cambios
        db.session.commit()
        
        # 4. Respuesta simple en pantalla
        return """
            <div style="font-family: sans-serif; text-align: center; margin-top: 50px;">
                <h1 style="color: #10b981;">🧹 ¡Historial de Materias Primas Borrado!</h1>
                <p>Las tablas de movimientos y ajustes han sido vaciadas con éxito y están listas para arrancar en limpio.</p>
                <br>
                <a href="/historial_materias_primas" style="display: inline-block; padding: 10px 20px; background: #059669; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">Volver al Historial</a>
            </div>
        """
    except Exception as e:
        db.session.rollback()
        return f"<h1>❌ Error al intentar vaciar el historial</h1><p>{str(e)}</p>", 500

# ==========================================
# EXPORTACIONES EXCLUSIVAS MATERIAS PRIMAS
# ==========================================

@app.route('/materias_primas/exportar_stock')
@login_required
def exportar_stock_mp():
    # 1. Seguridad exclusiva de MP
    if current_user.rol not in ['admin', 'jefe_materias_primas', 'encargado', 'consultas']:
        flash("⚠️ No tienes permisos para exportar este inventario.", "error")
        return redirect(request.referrer or url_for('materias_primas'))

    # 2. Búsqueda fija al sector materias_primas
    items = Item.query.join(Ubicacion).join(Rack).filter(
        Rack.sector == 'materias_primas',
        Item.cantidad > 0,
        Producto.sku != 'SUBDIVISION_VACIA'
    ).all()
    
    data = []
    for i in items:
        data.append({
            'Rack': i.ubicacion.rack.nombre,
            'Nivel': i.ubicacion.nivel,
            'Posicion': i.ubicacion.posicion,
            'Ubicacion': i.ubicacion.codigo_unico.split('-ID')[0],
            'SKU': i.producto_detalle.sku,
            'Descripcion': i.producto_detalle.descripcion,
            'Lote': i.lote or '-',
            'Vencimiento': i.fecha_vencimiento or '-',
            'Cantidad': i.cantidad,
            'Sub-Ubicacion': i.sub_ubicacion or 'General'
        })

    if not data:
        flash("⚠️ No hay stock cargado en Materias Primas para exportar.", "info")
        return redirect(request.referrer or url_for('materias_primas'))

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventario MP')

    output.seek(0)
    fecha = datetime.now().strftime("%d-%m-%Y")
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=f'Stock_Materias_Primas_{fecha}.xlsx')


@app.route('/materias_primas/exportar_busqueda')
@login_required
def exportar_busqueda_mp():
    # 1. Seguridad exclusiva de MP
    if current_user.rol not in ['admin', 'jefe_materias_primas', 'encargado', 'consultas']:
        flash("🚫 Acceso denegado: Tu perfil no tiene permisos para exportar.", "error")
        return redirect(request.referrer or url_for('materias_primas'))

    termino = request.args.get('q', '').strip().upper()
    f_sku = request.args.get('f_sku', '').strip().upper()
    f_desc = request.args.get('f_desc', '').strip().upper()
    f_estado = request.args.get('f_estado', '').strip().lower()

    if not termino:
        flash("⚠️ No hay ningún filtro aplicado para exportar.", "error")
        return redirect(request.referrer or url_for('materias_primas'))

    # 2. Búsqueda fija al sector materias_primas
    query = Item.query.join(Producto).join(Ubicacion).join(Rack).filter(Rack.sector == 'materias_primas')
    
    query = query.filter(db.or_(
        Producto.sku.ilike(f"{termino}%"),
        Producto.descripcion.ilike(f"%{termino}%"),
        Item.lote.ilike(f"{termino}%")
    ))
    
    if f_sku: query = query.filter(Producto.sku.ilike(f"%{f_sku}%"))
    if f_desc: query = query.filter(Producto.descripcion.ilike(f"%{f_desc}%"))
    if f_estado: query = query.filter(Item.estado_calidad == f_estado)

    resultados = query.order_by(Producto.sku.asc()).all()

    if not resultados:
        flash("No hay resultados para exportar.", "info")
        return redirect(request.referrer or url_for('materias_primas'))

    data = []
    for i in resultados:
        data.append({
            'SKU': i.producto_detalle.sku,
            'Descripción': i.producto_detalle.descripcion,
            'Lote / Partida': i.lote or '-',
            'Vencimiento': i.fecha_vencimiento or '-',
            'Cantidad': i.cantidad,
            'Estante Físico': i.ubicacion.codigo_unico.split('-ID')[0],
            'Sub-Ubicación / Caja': i.sub_ubicacion or 'General',
            'Estado': i.estado_calidad.upper()
        })

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Resultados MP')
        
        worksheet = writer.sheets['Resultados MP']
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length: max_length = len(cell.value)
                except: pass
            worksheet.column_dimensions[column].width = min(max_length + 2, 60)

    output.seek(0)
    fecha_hoy = datetime.now().strftime("%d-%m-%Y_%H%M")

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Busqueda_MP_{termino}_{fecha_hoy}.xlsx'
    )


@app.route('/importar_productos_mp', methods=['POST'])
@login_required
def importar_productos_mp():
    if current_user.rol not in ['admin', 'jefe_materias_primas']:
        flash('⚠️ No tienes permisos.', 'error')
        return redirect(request.referrer)

    archivo = request.files.get('archivo_csv')
    if not archivo or archivo.filename == '':
        flash('❌ No se seleccionó ningún archivo.', 'error')
        return redirect(request.referrer)

    try:
        import csv
        raw_data = archivo.read()
        try:
            texto = raw_data.decode('utf-8-sig')
        except:
            texto = raw_data.decode('latin1')

        lineas = texto.splitlines()
        delimitador = ';' if ';' in lineas[0] else ','
        lector = csv.reader(lineas, delimiter=delimitador)
        next(lector, None) # Saltar encabezado

        agregados = 0
        actualizados = 0

        for fila in lector:
            # Soportamos hasta 4 columnas: SKU, EAN, Descripción, Modelo
            if len(fila) >= 3:
                sku = str(fila[0]).strip().upper()
                ean = str(fila[1]).strip() if fila[1] else None
                desc = str(fila[2]).strip()
                modelo = str(fila[3]).strip() if len(fila) > 3 else ""
                
                if not sku: continue

                prod = Producto.query.filter_by(sku=sku, sector='materias_primas').first()
                if prod:
                    prod.ean = ean
                    prod.descripcion = desc
                    prod.modelo = modelo
                    actualizados += 1
                else:
                    nuevo = Producto(sku=sku, ean=ean, descripcion=desc, modelo=modelo, sector='materias_primas')
                    db.session.add(nuevo)
                    agregados += 1

        db.session.commit()
        flash(f'✅ Nómina MP actualizada: {agregados} nuevos, {actualizados} actualizados.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Error: {str(e)}', 'error')
        
    return redirect(request.referrer)

@app.route('/api/despiece/<int:orden_id>')
@login_required
def api_despiece(orden_id):
    # ==========================================
    # MODO DEBUG (RAYOS X) - INICIO
    # ==========================================
    print(f"\n{'='*50}")
    print(f"🚀 INICIANDO DESPIECE - ORDEN ID: {orden_id}")
    print(f"{'='*50}")

    orden = OrdenProduccion.query.get_or_404(orden_id)
    if orden.sku != 'A MEDIDA':
        print("❌ ERROR: El SKU de la orden no es 'A MEDIDA'")
        return jsonify({'status': 'error', 'message': 'Solo para pedidos A MEDIDA.'})

    desc_orden = orden.descripcion.upper()
    print(f"📄 DESCRIPCIÓN ORIGINAL: {orden.descripcion}")
    print(f"🔠 DESCRIPCIÓN MAYÚSCULAS: {desc_orden}")
    
    # Busca medidas incluso si el vendedor pone "120 * 150" o "120,5 x 150.2"
    match_medidas = re.search(r'(\d+(?:[\.,]\d+)?)\s*[xX\*]\s*(\d+(?:[\.,]\d+)?)', desc_orden)
    
    faltan_medidas = False
    try:
        if match_medidas:
            ancho_raw = match_medidas.group(1).replace(',', '.')
            alto_raw = match_medidas.group(2).replace(',', '.')
            ancho_val = str(float(ancho_raw))
            alto_val = str(float(alto_raw))
            print(f"📏 MEDIDAS DETECTADAS: Ancho={ancho_val}, Alto={alto_val}")
        else:
            faltan_medidas = True
            ancho_val = "0.0"
            alto_val = "0.0"
            print("⚠️ ADVERTENCIA: No se detectaron medidas en la descripción.")
            
        cant_val = str(float(orden.cantidad))
        unid_val = "2.0" if "DOBLE" in desc_orden else "1.0"
        print(f"🔢 VARIABLES: Cantidad={cant_val}, Unid={unid_val} (Doble? {'Sí' if unid_val=='2.0' else 'No'})")

        despiece_final = []

        config_prod = ConfiguracionProduccion.query.first()
        sku_maestro = config_prod.sku_maestro_a_medida if config_prod and config_prod.sku_maestro_a_medida else 'CORT9999'

        maestro = Producto.query.filter(Producto.sku.ilike(sku_maestro), Producto.sector == 'logistica').first()
        if not maestro:
            return jsonify({'status': 'error', 'message': f'No se encontró el SKU maestro {sku_maestro}.'})
        
        receta = Receta.query.filter_by(producto_final_id=maestro.id).all()

        for item in receta:
            mp = item.insumo
            if not mp: continue

            # --- FILTRO POR CONDICIÓN (SOPORTA EXCLUSIONES) ---
            if item.condicion:
                condiciones = [c.strip().upper() for c in item.condicion.split(',')]
                cumple_todas = True
                for c in condiciones:
                    if c.startswith('!') or c.startswith('-'):
                        palabra_prohibida = c[1:].strip()
                        if palabra_prohibida in desc_orden:
                            cumple_todas = False
                            break
                    else:
                        if c not in desc_orden:
                            cumple_todas = False
                            break
                if not cumple_todas:
                    continue

            # --- CÁLCULO DE FÓRMULA Y CORTE FÍSICO ---
            texto_formula = item.formula if item.formula else "Fija"
            consumo_calculado = 0.0
            texto_corte = "-" 
            
            if faltan_medidas and item.formula:
                texto_formula = "⚠️ Faltan medidas en el pedido"
            elif item.formula:
                ecuacion_base = item.formula.upper().replace(',', '.')
                ecuacion_base = ecuacion_base.replace(' X ', ' * ').replace('X', '*')
                
                # ✂️ TRADUCTOR DE CORTE MEJORADO PARA EL OPERARIO
                try:
                    corte_ancho_val = None
                    corte_alto_val = None

                    if "ANCHO" in ecuacion_base:
                        corte_ancho_val = float(ancho_val)
                        # Busca sumas/restas ej: (ANCHO - 3)
                        m_suma = re.search(r'\(?ANCHO\s*([+-]\s*\d+(?:\.\d+)?)\)?', ecuacion_base)
                        # Busca multiplicaciones ej: (ANCHO / 100) * 1.5
                        m_mult = re.search(r'\(?ANCHO\s*/\s*100\)?\s*\*\s*(\d+(?:\.\d+)?)', ecuacion_base)
                        
                        if m_suma:
                            corte_ancho_val += float(m_suma.group(1).replace(' ',''))
                        elif m_mult:
                            corte_ancho_val *= float(m_mult.group(1))

                    if "ALTO" in ecuacion_base:
                        corte_alto_val = float(alto_val)
                        # Busca sumas/restas ej: (ALTO + 30)
                        m_suma = re.search(r'\(?ALTO\s*([+-]\s*\d+(?:\.\d+)?)\)?', ecuacion_base)
                        # Busca multiplicaciones ej: (ALTO / 100) * 1.5
                        m_mult = re.search(r'\(?ALTO\s*/\s*100\)?\s*\*\s*(\d+(?:\.\d+)?)', ecuacion_base)
                        
                        if m_suma:
                            corte_alto_val += float(m_suma.group(1).replace(' ',''))
                        elif m_mult:
                            corte_alto_val *= float(m_mult.group(1))

                    # Armamos el texto que va a ver el operario en la tabla (La g limpia el .0 al final)
                    if corte_ancho_val is not None and corte_alto_val is not None and "ANCHO" in ecuacion_base and "ALTO" in ecuacion_base:
                        texto_corte = f"Cortar: {corte_ancho_val:g}cm x {corte_alto_val:g}cm"
                    elif corte_ancho_val is not None:
                        texto_corte = f"Cortar a: {corte_ancho_val:g}cm"
                    elif corte_alto_val is not None:
                        texto_corte = f"Cortar a: {corte_alto_val:g}cm"
                except:
                    texto_corte = "Ver fórmula"

                # 🧮 CÁLCULO DE CONSUMO PARA DESCONTAR DEL STOCK
                ecuacion = ecuacion_base.replace('ANCHO', ancho_val)
                ecuacion = ecuacion.replace('ALTO', alto_val)
                ecuacion = ecuacion.replace('CANTIDAD', cant_val)
                ecuacion = ecuacion.replace('UNID', unid_val)
                
                # Aspiradora de letras
                ecuacion = ecuacion.replace('CM', '').replace('MTS', '')
                ecuacion_limpia = re.sub(r'[A-Z]', '', ecuacion).strip()
                
                try:
                    consumo_calculado = float(eval(ecuacion_limpia))
                    texto_formula = item.formula
                except Exception as e:
                    texto_formula = f"Error DB: {ecuacion_limpia}"
                    consumo_calculado = 0.0
            else:
                consumo_calculado = float(item.cantidad_necesaria) * float(cant_val)
                texto_formula = "Cantidad Fija"

            despiece_final.append({
                'sku': mp.sku,
                'material': mp.descripcion,
                'corte': texto_corte, 
                'formula': texto_formula,
                'consumo': f"{consumo_calculado:.2f} {item.unidad_medida or 'u'}"
            })

        if faltan_medidas:
            despiece_final.insert(0, {
                'sku': 'AVISO', 'material': 'El vendedor no incluyó las medidas (Ej: 120x150) en la descripción.', 'corte': '-', 'formula': '-', 'consumo': '0'
            })

        return jsonify({'status': 'ok', 'despiece': despiece_final, 'cantidad': orden.cantidad})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': f"Error crítico: {str(e)}"})

@app.route('/magia_orden_receta')
def magia_orden_receta():
    try:
        db.session.execute(db.text("ALTER TABLE receta ADD COLUMN orden INTEGER DEFAULT 0"))
        db.session.commit()
        return "<h1>✅ ¡Éxito!</h1><p>Ahora puedes ordenar tus recetas.</p>"
    except Exception as e:
        return f"<h1>⚠️ Aviso</h1><p>Quizás ya existía: {str(e)}</p>"

@app.route('/ingenieria/guardar_orden_receta', methods=['POST'])
@login_required
def guardar_orden_receta():
    data = request.get_json()
    orden_ids = data.get('orden', [])

    for index, receta_id in enumerate(orden_ids):
        item = Receta.query.get(receta_id)
        if item:
            item.orden = index
    
    db.session.commit()
    return jsonify({'status': 'ok'})
    

@app.route('/reparar_receta')
def reparar_receta():
    try:
        # Agregamos la columna formula
        db.session.execute(db.text("ALTER TABLE receta ADD COLUMN formula VARCHAR(150)"))
    except Exception as e:
        print(f"Aviso formula: {e}")
        pass
        
    try:
        # Agregamos la columna condicion
        db.session.execute(db.text("ALTER TABLE receta ADD COLUMN condicion VARCHAR(150)"))
    except Exception as e:
        print(f"Aviso condicion: {e}")
        pass

    try:
        # Agregamos la columna orden para el Drag & Drop
        db.session.execute(db.text("ALTER TABLE receta ADD COLUMN orden INTEGER DEFAULT 0"))
    except Exception as e:
        print(f"Aviso orden: {e}")
        pass

    db.session.commit()
    return """
        <div style="font-family: sans-serif; text-align: center; margin-top: 50px;">
            <h1 style="color: #10b981;">✅ ¡Base de datos reparada!</h1>
            <p>Las columnas formula, condicion y orden ya están integradas.</p>
            <a href="/ingenieria" style="display: inline-block; padding: 10px 20px; background: #0ea5e9; color: white; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 20px;">Volver a Ingeniería</a>
        </div>
    """

@app.route('/estirar_roles')
def estirar_roles():
    try:
        from sqlalchemy import text
        db.session.execute(text("ALTER TABLE usuario MODIFY COLUMN rol VARCHAR(100)"))
        db.session.commit()
        return "<h1>✅ ¡Éxito!</h1><p>La columna de roles ahora soporta hasta 100 letras. Ya podés crear al supervisor.</p>"
    except Exception as e:
        return f"<h1>⚠️ Aviso</h1><p>Hubo un problema o ya estaba arreglado: {str(e)}</p>"

if __name__ == '__main__':
    print("Iniciando WMS Profesional en puerto 5001...")
    with app.app_context():
        db.create_all()  # Esto crea las tablas que falten sin borrar las actuales
    app.run(host='0.0.0.0', port=5001, debug=True)
    