import os
from datetime import datetime, timedelta, timezone
from pymongo.results import UpdateResult
from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError


load_dotenv()

app = Flask(__name__)

MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "reserva_boletos").strip()
RESERVA_SEGUNDOS = max(15, min(int(os.getenv("RESERVA_SEGUNDOS", "60")), 1800))

cliente_mongo = None
boletos = None
reservas= None

def ahora_utc():
    return datetime.now(timezone.utc)


def obtener_coleccion():
    global cliente_mongo, boletos, reservas

    if not MONGO_URI:
        raise RuntimeError(
            "Falta MONGO_URI. Copia .env.example como .env y agrega tu conexion de MongoDB Atlas."
        )

    if cliente_mongo is None:
        cliente_mongo = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=10000,
            tz_aware=True,
        )
        base_datos = cliente_mongo[MONGO_DB]
        boletos = base_datos["boletos"]
        
        reservas = base_datos["reservas"]
        reservas.create_index("reserva_id")
        reservas.create_index([("estado", ASCENDING), ("vence_en", ASCENDING)])
        
        boletos.create_index(
            [("evento", ASCENDING), ("asiento", ASCENDING)],
            unique=True,
            name="evento_asiento_unico",
        )
        boletos.create_index("estado")
        boletos.create_index("reserva_hasta")

    return boletos


def convertir_fecha(valor):
    if not valor:
        return None

    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)

    return valor.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def serializar_boleto(documento):
    if not documento:
        return None

    return {
        "_id": str(documento["_id"]),
        "evento": documento.get("evento", ""),
        "zona": documento.get("zona", ""),
        "asiento": documento.get("asiento", ""),
        "precio": documento.get("precio", 0),
        "estado": documento.get("estado", "disponible"),
        "reservado_por": documento.get("reservado_por"),
        "reservado_nombre": documento.get("reservado_nombre"),
        "reserva_hasta": convertir_fecha(documento.get("reserva_hasta")),
        "comprado_por": documento.get("comprado_por"),
        "comprado_nombre": documento.get("comprado_nombre"),
        "comprado_en": convertir_fecha(documento.get("comprado_en")),
        "creado_en": convertir_fecha(documento.get("creado_en")),
        "actualizado_en": convertir_fecha(documento.get("actualizado_en")),
    }


def limpiar_texto(valor):
    return " ".join(str(valor or "").strip().split())


def obtener_object_id(valor):
    try:
        return ObjectId(valor)
    except (InvalidId, TypeError):
        raise ValueError("El identificador del boleto no es valido.")


def validar_datos_boleto(datos):
    evento = limpiar_texto(datos.get("evento"))
    zona = limpiar_texto(datos.get("zona"))
    asiento = limpiar_texto(datos.get("asiento")).upper()

    try:
        precio = float(datos.get("precio"))
    except (TypeError, ValueError):
        raise ValueError("El precio debe ser numerico.")

    if len(evento) < 3 or len(evento) > 80:
        raise ValueError("El evento debe tener entre 3 y 80 caracteres.")

    if len(zona) < 2 or len(zona) > 40:
        raise ValueError("La zona debe tener entre 2 y 40 caracteres.")

    if not asiento or len(asiento) > 20:
        raise ValueError("Escribe un asiento valido de hasta 20 caracteres.")

    if precio < 0:
        raise ValueError("El precio no puede ser negativo.")

    return {
        "evento": evento,
        "zona": zona,
        "asiento": asiento,
        "precio": round(precio, 2),
    }


def obtener_cliente(datos):
    cliente_id = limpiar_texto(datos.get("cliente_id"))
    cliente_nombre = limpiar_texto(datos.get("cliente_nombre"))

    if not cliente_id or not cliente_nombre:
        raise ValueError("Es necesario identificar al comprador.")

    return cliente_id, cliente_nombre


def liberar_reservas_vencidas():
    coleccion = obtener_coleccion()

    pendientes = list(reservas.find(
        {
            "estado": "activa",
            "vence_en": {"$lte": ahora_utc()},
        },
        {"_id": 1},
    ))

    liberadas = 0

    with cliente_mongo.start_session() as sesion:
        for pendiente in pendientes:

            def liberar(sesion):
                momento = ahora_utc()

                reserva = reservas.find_one_and_update(
                    {
                        "_id": pendiente["_id"],
                        "estado": "activa",
                        "vence_en": {"$lte": momento},
                    },
                    {
                        "$set": {
                            "estado": "vencida",
                            "cerrada_en": momento,
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                    session=sesion,
                )

                if reserva is None:
                    return 0

                resultado = coleccion.update_one(
                    {
                        "_id": reserva["recurso_id"],
                        "estado": "reservado",
                        "reservado_por": reserva["reserva_id"],
                        "reserva_hasta": reserva["vence_en"],
                    },
                    {
                        "$set": {
                            "estado": "disponible",
                            "reservado_por": None,
                            "reservado_nombre": None,
                            "reserva_hasta": None,
                            "actualizado_en": momento,
                        }
                    },
                    session=sesion,
                )

                return resultado.modified_count

            liberadas += sesion.with_transaction(liberar)

    return UpdateResult(
        {"n": liberadas, "nModified": liberadas, "ok": 1},
        acknowledged=True,
    )


@app.get("/")
def inicio():
    return render_template("index.html", reserva_segundos=RESERVA_SEGUNDOS)


@app.get("/api/salud")
def salud():
    coleccion = obtener_coleccion()
    coleccion.database.client.admin.command("ping")

    return jsonify(
        {
            "ok": True,
            "mensaje": "Flask y MongoDB Atlas estan funcionando.",
            "fecha": convertir_fecha(ahora_utc()),
        }
    )


@app.get("/api/boletos")
def listar_boletos():
    liberar_reservas_vencidas()
    coleccion = obtener_coleccion()
    documentos = coleccion.find().sort([("evento", ASCENDING), ("asiento", ASCENDING)])
    return jsonify([serializar_boleto(documento) for documento in documentos])


@app.post("/api/boletos/semilla")
def crear_semilla():
    coleccion = obtener_coleccion()
    momento = ahora_utc()
    ejemplos = [
        ("Festival escolar", "General", "A-01", 150),
        ("Festival escolar", "General", "A-02", 150),
        ("Festival escolar", "General", "A-03", 150),
        ("Festival escolar", "Preferente", "B-01", 250),
        ("Festival escolar", "Preferente", "B-02", 250),
        ("Festival escolar", "Preferente", "B-03", 250),
    ]

    for evento, zona, asiento, precio in ejemplos:
        coleccion.update_one(
            {"evento": evento, "asiento": asiento},
            {
                "$setOnInsert": {
                    "evento": evento,
                    "zona": zona,
                    "asiento": asiento,
                    "precio": precio,
                    "estado": "disponible",
                    "reservado_por": None,
                    "reservado_nombre": None,
                    "reserva_hasta": None,
                    "comprado_por": None,
                    "comprado_nombre": None,
                    "comprado_en": None,
                    "creado_en": momento,
                    "actualizado_en": momento,
                }
            },
            upsert=True,
        )

    return jsonify({"mensaje": "Se prepararon los seis boletos de ejemplo."}), 201


@app.post("/api/boletos/liberar-vencidas")
def liberar_vencidas():
    resultado = liberar_reservas_vencidas()
    return jsonify(
        {
            "mensaje": "Revision de reservas terminada.",
            "liberadas": resultado.modified_count,
        }
    )


@app.post("/api/boletos")
def crear_boleto():
    coleccion = obtener_coleccion()
    datos = validar_datos_boleto(request.get_json(silent=True) or {})
    momento = ahora_utc()
    documento = {
        **datos,
        "estado": "disponible",
        "reservado_por": None,
        "reservado_nombre": None,
        "reserva_hasta": None,
        "comprado_por": None,
        "comprado_nombre": None,
        "comprado_en": None,
        "creado_en": momento,
        "actualizado_en": momento,
    }
    resultado = coleccion.insert_one(documento)
    documento["_id"] = resultado.inserted_id

    return jsonify(
        {
            "mensaje": "Boleto creado correctamente.",
            "boleto": serializar_boleto(documento),
        }
    ), 201


@app.put("/api/boletos/<boleto_id>")
def editar_boleto(boleto_id):
    coleccion = obtener_coleccion()
    datos = validar_datos_boleto(request.get_json(silent=True) or {})
    datos["actualizado_en"] = ahora_utc()

    documento = coleccion.find_one_and_update(
        {
            "_id": obtener_object_id(boleto_id),
            "estado": "disponible",
        },
        {"$set": datos},
        return_document=ReturnDocument.AFTER,
    )

    if not documento:
        return jsonify(
            {"mensaje": "Solo se pueden editar boletos disponibles."}
        ), 409

    return jsonify(
        {
            "mensaje": "Boleto actualizado correctamente.",
            "boleto": serializar_boleto(documento),
        }
    )


@app.delete("/api/boletos/<boleto_id>")
def eliminar_boleto(boleto_id):
    coleccion = obtener_coleccion()
    documento = coleccion.find_one_and_delete(
        {
            "_id": obtener_object_id(boleto_id),
            "estado": "disponible",
        }
    )

    if not documento:
        return jsonify(
            {"mensaje": "Solo se pueden eliminar boletos disponibles."}
        ), 409

    return jsonify({"mensaje": "Boleto eliminado correctamente."})

@app.post("/api/boletos/<boleto_id>/reservar")
def reservar_boleto(boleto_id):
    coleccion = obtener_coleccion()
    datos = request.get_json(silent=True) or {}

    cliente_id, cliente_nombre = obtener_cliente(datos)
    identificador = obtener_object_id(boleto_id)
    momento = ahora_utc()
    reserva_hasta = momento + timedelta(seconds=RESERVA_SEGUNDOS)

    with cliente_mongo.start_session() as sesion:

        def guardar_reserva(sesion):
            documento = coleccion.find_one_and_update(
                {
                    "_id": identificador,
                    "$or": [
                        {"estado": "disponible"},
                        {
                            "estado": "reservado",
                            "reserva_hasta": {"$lte": momento},
                        },
                    ],
                },
                {
                    "$set": {
                        "estado": "reservado",
                        "reservado_por": cliente_id,
                        "reservado_nombre": cliente_nombre,
                        "reserva_hasta": reserva_hasta,
                        "comprado_por": None,
                        "comprado_nombre": None,
                        "comprado_en": None,
                        "actualizado_en": momento,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=sesion,
            )

            if documento is None:
                return None

            # Cierra apartados anteriores que ya vencieron.
            reservas.update_many(
                {
                    "recurso_id": identificador,
                    "estado": "activa",
                    "vence_en": {"$lte": momento},
                },
                {"$set": {"estado": "vencida"}},
                session=sesion,
            )

            reservas.insert_one(
                {
                    "reserva_id": cliente_id,
                    "recurso_id": identificador,
                    "cantidad": 1,
                    "estado": "activa",
                    "creada_en": momento,
                    "vence_en": reserva_hasta,
                },
                session=sesion,
            )

            return documento

        documento = sesion.with_transaction(guardar_reserva)

    if documento is None:
        return jsonify({
            "mensaje": "El boleto ya fue reservado o comprado."
        }), 409

    return jsonify({
        "mensaje": f"Boleto reservado durante {RESERVA_SEGUNDOS} segundos.",
        "boleto": serializar_boleto(documento),
    })


@app.post("/api/boletos/<boleto_id>/cancelar")
def cancelar_reserva(boleto_id):
    coleccion = obtener_coleccion()
    datos = request.get_json(silent=True) or {}

    cliente_id = limpiar_texto(datos.get("cliente_id"))
    if not cliente_id:
        raise ValueError("Falta identificar al comprador.")

    identificador = obtener_object_id(boleto_id)

    with cliente_mongo.start_session() as sesion:

        def cancelar(sesion):
            momento = ahora_utc()

            reserva = reservas.find_one_and_update(
                {
                    "recurso_id": identificador,
                    "reserva_id": cliente_id,
                    "estado": "activa",
                },
                {
                    "$set": {
                        "estado": "cancelada",
                        "cerrada_en": momento,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=sesion,
            )

            if reserva is None:
                return None

            documento = coleccion.find_one_and_update(
                {
                    "_id": identificador,
                    "estado": "reservado",
                    "reservado_por": cliente_id,
                },
                {
                    "$set": {
                        "estado": "disponible",
                        "reservado_por": None,
                        "reservado_nombre": None,
                        "reserva_hasta": None,
                        "actualizado_en": momento,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=sesion,
            )

            if documento is None:
                # El error revierte también el cierre de la reserva.
                raise ValueError(
                    "El boleto ya no corresponde a esta reserva."
                )

            return documento

        documento = sesion.with_transaction(cancelar)

    if documento is None:
        return jsonify({
            "mensaje": "No tienes una reserva activa para este boleto."
        }), 409

    return jsonify({
        "mensaje": "Reserva cancelada. El boleto vuelve a estar disponible.",
        "boleto": serializar_boleto(documento),
    })


@app.post("/api/boletos/<boleto_id>/confirmar")
def confirmar_compra(boleto_id):
    coleccion = obtener_coleccion()
    datos = request.get_json(silent=True) or {}

    cliente_id, clien
    te_nombre = obtener_cliente(datos)
    identificador = obtener_object_id(boleto_id)

    with cliente_mongo.start_session() as sesion:

        def confirmar(sesion):
            momento = ahora_utc()

            reserva = reservas.find_one_and_update(
                {
                    "recurso_id": identificador,
                    "reserva_id": cliente_id,
                    "estado": "activa",
                    "vence_en": {"$gt": momento},
                },
                {
                    "$set": {
                        "estado": "confirmada",
                        "cerrada_en": momento,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=sesion,
            )

            if reserva is None:
                return None

            documento = coleccion.find_one_and_update(
                {
                    "_id": identificador,
                    "estado": "reservado",
                    "reservado_por": cliente_id,
                    "reserva_hasta": reserva["vence_en"],
                    "$expr": {
                        "$gt": ["$reserva_hasta", momento]
                    },
                },
                {
                    "$set": {
                        "estado": "comprado",
                        "comprado_por": cliente_id,
                        "comprado_nombre": cliente_nombre,
                        "comprado_en": momento,
                        "reservado_por": None,
                        "reservado_nombre": None,
                        "reserva_hasta": None,
                        "actualizado_en": momento,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=sesion,
            )

            if documento is None:
                raise ValueError(
                    "El boleto ya no corresponde a esta reserva."
                )

            # Registro final de la compra.
            coleccion.database["compras"].insert_one(
                {
                    "reserva_documento_id": reserva["_id"],
                    "reserva_id": cliente_id,
                    "recurso_id": identificador,
                    "cliente_nombre": cliente_nombre,
                    "cantidad": 1,
                    "precio": documento["precio"],
                    "estado": "confirmada",
                    "creada_en": momento,
                },
                session=sesion,
            )

            return documento

        documento = sesion.with_transaction(confirmar)

    if documento is None:
        liberar_reservas_vencidas()

        return jsonify({
            "mensaje": (
                "La reserva venció, ya fue cerrada "
                "o pertenece a otro usuario."
            )
        }), 409

    return jsonify({
        "mensaje": "Compra confirmada correctamente.",
        "boleto": serializar_boleto(documento),
    })

@app.errorhandler(ValueError)
def manejar_valor_invalido(error):
    return jsonify({"mensaje": str(error)}), 400


@app.errorhandler(DuplicateKeyError)
def manejar_duplicado(error):
    return jsonify(
        {"mensaje": "Ya existe un boleto con el mismo evento y asiento."}
    ), 409


@app.errorhandler(RuntimeError)
def manejar_configuracion(error):
    return jsonify({"mensaje": str(error)}), 500


@app.errorhandler(PyMongoError)
def manejar_mongo(error):
    app.logger.exception(error)
    return jsonify(
        {"mensaje": "No fue posible comunicarse con MongoDB Atlas."}
    ), 500


@app.errorhandler(404)
def no_encontrado(error):
    if request.path.startswith("/api/"):
        return jsonify({"mensaje": "Ruta no encontrada."}), 404
    return error


if __name__ == "__main__":
    puerto = int(os.getenv("PORT", "5000"))
    modo_debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=puerto, debug=modo_debug)
