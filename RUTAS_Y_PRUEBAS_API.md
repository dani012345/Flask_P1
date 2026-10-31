# Rutas y pruebas de la API

La interfaz ya utiliza estas rutas. Tambien pueden probarse con Postman,
Thunder Client o Insomnia.

## Crear boleto

```http
POST /api/boletos
Content-Type: application/json
```

```json
{
  "evento": "Festival escolar",
  "zona": "General",
  "asiento": "C-01",
  "precio": 180
}
```

## Reservar

```http
POST /api/boletos/ID_DEL_BOLETO/reservar
Content-Type: application/json
```

```json
{
  "cliente_id": "usuario-1",
  "cliente_nombre": "Ana Perez"
}
```

## Cancelar

```http
POST /api/boletos/ID_DEL_BOLETO/cancelar
Content-Type: application/json
```

```json
{
  "cliente_id": "usuario-1",
  "cliente_nombre": "Ana Perez"
}
```

## Confirmar compra

```http
POST /api/boletos/ID_DEL_BOLETO/confirmar
Content-Type: application/json
```

```json
{
  "cliente_id": "usuario-1",
  "cliente_nombre": "Ana Perez"
}
```

## Casos de prueba

| Caso | Resultado esperado |
|---|---|
| Reservar disponible | Respuesta 200 y estado reservado |
| Reservar desde otro usuario | Respuesta 409 |
| Cancelar reserva propia | Regresa a disponible |
| Cancelar reserva ajena | Respuesta 409 |
| Confirmar antes de vencer | Cambia a comprado |
| Confirmar despues de vencer | Respuesta 409 |
| Editar reservado | Respuesta 409 |
| Eliminar comprado | Respuesta 409 |
