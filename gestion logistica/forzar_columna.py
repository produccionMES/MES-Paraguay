from app import app, db
from sqlalchemy import text

with app.app_context():
    try:
        # Le inyectamos la columna directo al corazón de la base de datos conectada
        db.session.execute(text("ALTER TABLE rack ADD COLUMN proposito VARCHAR(50)"))
        db.session.commit()
        print("✅ ¡Éxito total! La columna 'proposito' se inyectó en la base de datos correcta.")
    except Exception as e:
        # Si tira error de duplicado, es porque ya existía
        print(f"⚠️ Resultado de la operación: {e}")