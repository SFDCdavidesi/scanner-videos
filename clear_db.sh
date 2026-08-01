#!/bin/bash
echo "⚠️  ¿Estás seguro de que deseas eliminar todas las películas del fichero de base de datos (videos_db.json)? (s/n)"
read -r respuesta

if [ "$respuesta" = "s" ] || [ "$respuesta" = "S" ]; then
    # Realiza una copia de seguridad por seguridad antes de vaciarlo
    if [ -f "videos_db.json" ]; then
        cp videos_db.json videos_db.json.bak
        echo "📦 Se ha creado una copia de seguridad en videos_db.json.bak"
    fi
    
    # Vacía el archivo asignándole una lista JSON vacía
    echo "[]" > videos_db.json
    echo "✅ ¡Base de datos videos_db.json vaciada correctamente!"
else
    echo "❌ Operación cancelada."
fi
