from app import app, db
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("ALTER TABLE rack ADD COLUMN color VARCHAR(20)"))
        db.session.commit()
        print("✅ Columna 'color' agregada con éxito.")
    except Exception as e:
        print(f"⚠️ Nota: {e}")