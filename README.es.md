# Servidor MCP de Mercado Libre

[🇺🇸 English](README.md) | [🇪🇸 Español](README.es.md)

Un servidor [Model Context Protocol (MCP)](https://modelcontextprotocol.io) que envuelve la [API REST de Mercado Libre](https://developers.mercadolibre.com) — permitiendo a asistentes de IA buscar, crear, actualizar, eliminar y gestionar publicaciones, órdenes, envíos, preguntas, campañas publicitarias y más en **18 países** de Latinoamérica.

## ¿Por qué?

Mercado Libre ya publica un [servidor MCP oficial](https://developers.mercadolibre.com.uy/es_ar/mcp-server), pero solo expone **herramientas de búsqueda en documentación** — no puede interactuar con la API por ti. Este servidor llena ese vacío envolviendo **más de 130 endpoints REST** como herramientas MCP que un asistente de IA puede llamar directamente.

Las credenciales y tokens se cargan desde variables de entorno y **nunca pasan por el prompt del LLM**, manteniendo tus claves seguras.

## Funcionalidades

- **Publicaciones (CRUD)** — buscar, ver, crear, actualizar, cerrar, republicar artículos
- **Multi-país** — 18 sitios: Argentina (MLA), Uruguay (MLU), Brasil (MLB), México (MLM), Chile (MLC) y más
- **Órdenes** — buscar y ver órdenes de venta
- **Envíos** — obtener métodos de envío, rastrear envíos
- **Categorías** — explorar, ver detalles, predecir la mejor categoría para un producto
- **Preguntas** — listar y responder preguntas de compradores
- **Publicidad** — listar campañas de Mercado Ads
- **Métricas** — obtener visitas y estadísticas de artículos
- **Perfiles** — ver reputación de vendedor/comprador

## Fuentes y Documentación

Este servidor está construido sobre la API oficial de Mercado Libre:

| Recurso | URL |
|---|---|
| Portal de Desarrolladores | https://developers.mercadolibre.com |
| Documentación API (ES) | https://developers.mercadolibre.com.uy/es_ar/api-docs-es |
| Autenticación y OAuth | https://developers.mercadolibre.com.uy/es_ar/autenticacion-y-autorizacion |
| Ítems y Búsquedas | https://developers.mercadolibre.com.uy/es_ar/items-y-busquedas |
| Órdenes | https://developers.mercadolibre.com.uy/es_ar/gestiona-ventas |
| Envíos | https://developers.mercadolibre.com.uy/es_ar/mercado-envios |
| Mercado Ads | https://developers.mercadolibre.com.uy/es_ar/introduccion-a-mercado-ads |
| Límites de tasa | https://developers.mercadolibre.com.uy/es_ar/rate-limit-error-429 |

## Requisitos

- **Python 3.11+** con [uv](https://docs.astral.sh/uv/) instalado
- Una **cuenta vendedora de Mercado Libre**
- Una **Aplicación de Mercado Libre** (gratuita — se crea en el portal de desarrolladores)

## Instalación

```bash
# Clonar
git clone https://github.com/TU_USUARIO/mercadolibre-mcp.git
cd mercadolibre-mcp

# Instalar dependencias
uv sync

# Verificar que funciona
uv run python -c "from mercadolibre_mcp.main import mcp; print('Servidor MCP listo')"
```

## Configuración de Credenciales

### 1. Crear una Aplicación en Mercado Libre

1. Ve a [https://developers.mercadolibre.com/apps](https://developers.mercadolibre.com/apps)
2. Haz clic en **"Crear aplicación"**
3. Completa:
   - **Nombre de la Aplicación**: ej., `mercadolibre-mcp`
   - **Descripción**: Breve descripción de tu uso
   - **Redirect URI**: Usa `http://localhost:8080/callback` (para pruebas locales)
4. Al crearla, obtendrás:
   - **`App ID`** (client_id) — un ID numérico
   - **`Secret Key`** (client_secret) — una cadena alfanumérica larga
5. En **"Permisos funcionales"**, selecciona al menos:
   - `read` — para leer artículos, órdenes, etc.
   - `write` — para crear/actualizar publicaciones
   - `offline_access` — para que el token siga funcionando después de cerrar sesión
6. Guarda los cambios.

### 2. Configurar Variables de Entorno

Copia el archivo de ejemplo:

```bash
cp .env.example .env
```

Edita `.env` con tus credenciales:

```env
MERCADOLIBRE_CLIENT_ID=1234567890
MERCADOLIBRE_CLIENT_SECRET=tu_secret_key
MERCADOLIBRE_REDIRECT_URI=http://localhost:8080/callback
MERCADOLIBRE_SITE_ID=MLA
```

| Variable | Requerida | Descripción |
|---|---|---|
| `MERCADOLIBRE_CLIENT_ID` | ✅ Sí | Tu App ID del portal de desarrolladores |
| `MERCADOLIBRE_CLIENT_SECRET` | ✅ Sí | Tu Secret Key |
| `MERCADOLIBRE_REDIRECT_URI` | ✅ Sí | Debe coincidir con lo registrado en la app |
| `MERCADOLIBRE_SITE_ID` | ❌ No | Sitio **por defecto/respaldo** cuando una herramienta no especifica uno (ej. MLA, MLU). No es una restricción — ver configuración multi-país abajo. |
| `LOG_LEVEL` | ❌ No | `INFO`, `DEBUG`, `WARNING`, `ERROR` |

### 3. Ejecutar la Configuración OAuth — una vez por país

**Una sola app, múltiples tokens.** El `MERCADOLIBRE_CLIENT_ID` / `MERCADOLIBRE_CLIENT_SECRET` de arriba se comparten entre **los 18 países** — registrás la aplicación una sola vez, y los mismos valores de `.env` funcionan en todos lados. Sin embargo, el **token de acceso** que genera el flujo OAuth pertenece a una cuenta vendedora específica de Mercado Libre, y las cuentas normalmente están registradas bajo un único país de origen. Si vendés tanto en Argentina como en Uruguay con dos cuentas separadas, tenés que autorizar **cada una por separado** — mismas credenciales de app, dos tokens diferentes.

Ejecutá la configuración una vez por cada país en el que operás:

```bash
# Autorizar tu cuenta de Argentina
uv run python -m mercadolibre_mcp.auth --site-id MLA

# Autorizar tu cuenta de Uruguay
uv run python -m mercadolibre_mcp.auth --site-id MLU

# ...repetí para cualquier otro país/cuenta que tengas
```

Cada ejecución hará lo siguiente:
1. Abrirá tu navegador para autorizar la cuenta de ese país
2. Te pedirá que pegues la URL redirigida
3. Guardará un token de acceso en `~/.mercadolibre_mcp/profiles/<SITE_ID>.json` (ej. `MLA.json`, `MLU.json`)

Los tokens se guardan **localmente en tu máquina**, un archivo por país, y **nunca se envían al LLM**. El servidor MCP los usa del lado del servidor para autenticar las llamadas a la API, renovando cada uno automáticamente cuando expira.

Podés revisar qué países ya están autorizados en cualquier momento:

```bash
uv run python -m mercadolibre_mcp.auth --list
```

O simplemente preguntale a tu asistente de IA — *"¿En qué países de Mercado Libre estoy autenticado?"* — que usa la herramienta incorporada `list_authenticated_sites`.

Todas las herramientas aceptan un parámetro opcional `site_id` que elige qué perfil autenticado ejecuta la llamada (ej. "mostrame mis publicaciones en Uruguay" → `site_id="MLU"`); si se omite, usa `MERCADOLIBRE_SITE_ID` (o `MLA`) por defecto.

> **Avanzado / no usado acá:** Mercado Libre también ofrece un programa oficial de venta transfronteriza ["Global Selling"](https://global-selling.mercadolibre.com) donde una única cuenta de comerciante aprobada puede operar en México, Brasil, Chile, Colombia y Argentina con **un solo** token. Requiere un proceso de habilitación especial con Mercado Libre y **no** cubre oficialmente Uruguay, por lo que este servidor no lo utiliza — el flujo estándar por país de arriba funciona para cualquier vendedor sin inscripción especial.

---

## Configuración en Clientes

### Claude Desktop / Claude Code

Agrega a `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) o `~/.config/Claude/claude_desktop_config.json` (Linux):

```json
{
  "mcpServers": {
    "mercadolibre": {
      "command": "uv",
      "args": [
        "--directory",
        "/RUTA/ABSOLUTA/A/mercadolibre-mcp",
        "run",
        "python",
        "-m",
        "mercadolibre_mcp.main"
      ]
    }
  }
}
```

**Importante**: No pongas tus credenciales en el JSON de configuración. Van en el archivo `.env` o en tu perfil de shell:

```bash
# Agrega a ~/.zshrc o ~/.bashrc
export MERCADOLIBRE_CLIENT_ID="tu_app_id"
export MERCADOLIBRE_CLIENT_SECRET="tu_secret"
export MERCADOLIBRE_SITE_ID="MLA"
```

### Cursor

Agrega a `~/.cursor/mcp_config.json`:

```json
{
  "mcpServers": {
    "mercadolibre": {
      "command": "uv",
      "args": ["--directory", "/RUTA/ABSOLUTA/A/mercadolibre-mcp", "run", "python", "-m", "mercadolibre_mcp.main"]
    }
  }
}
```

### Windsurf

Agrega a `~/.windsurf/mcp_config.json` (mismo formato que Cursor).

### OpenCode

Configura en `.opencode/mcp_servers.json`:

```json
{
  "servers": {
    "mercadolibre": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/RUTA/ABSOLUTA/A/mercadolibre-mcp", "run", "python", "-m", "mercadolibre_mcp.main"]
    }
  }
}
```

### Muster (si usas el agregador Muster)

Crea `/Users/external-bruno.ponce/.config/muster/mcpservers/mercadolibre.yaml`:

```yaml
apiVersion: muster.giantswarm.io/v1alpha1
kind: MCPServer
metadata:
  name: mercadolibre
  namespace: default
spec:
  autoStart: true
  command: /Users/external-bruno.ponce/.local/bin/uv
  args:
    - --directory
    - /RUTA/ABSOLUTA/A/mercadolibre-mcp
    - run
    - python
    - -m
    - mercadolibre_mcp.main
  env:
    PATH: /opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
    MERCADOLIBRE_CLIENT_ID: tu_app_id
    MERCADOLIBRE_CLIENT_SECRET: tu_secret
    MERCADOLIBRE_SITE_ID: MLA
  timeout: 120
  type: stdio
```

---

## Ejemplos de Uso

Una vez conectado el servidor, puedes pedirle a tu asistente de IA cosas como:

### Búsqueda de Productos

> "Buscá iPhone 15 Pro Max en Argentina, menos de 2000 USD"
> "Buscá zapatillas running en Uruguay, entre 1000 y 5000 UYU"
> "Mostrame notebooks disponibles en Brasil"

### Gestión de Publicaciones

> "Creá una publicación: iPhone 15, 128GB, nuevo, 1500 ARS, categoría MLA1051, cantidad 5"
> "Actualizá el precio del artículo MLA1234567890 a 2000"
> "Cerrame la publicación MLB987654321"
> "Mostrame todas mis publicaciones activas"
> "Republicá mi artículo cerrado MLA1234567890"

### Categorías

> "¿Qué categorías hay disponibles en Uruguay?"
> "Decime sobre la categoría MLU1000"
> "¿Qué categoría me recomiendas para 'Zapatillas Nike Running Hombre'?"

### Órdenes y Envíos

> "Mostrame mis órdenes recientes"
> "¿Cuál es el estado de la orden 1234567890?"
> "Rastreá el envío 987654321"
> "¿Qué opciones de envío hay para el artículo MLA123 al código postal 11000?"

### Preguntas

> "Mostrame preguntas sin responder de mis artículos"
> "Respondé la pregunta 98765 con 'Sí, tenemos stock'"

### Publicidad y Analítica

> "Mostrá mis campañas activas de Mercado Ads"
> "¿Cuántas visitas tiene el artículo MLA1234567890?"
> "Mostrame las visitas de la semana pasada de mi artículo más vendido"

### Estado Multi-País

> "¿En qué países de Mercado Libre estoy autenticado?"
> "¿Ya estoy configurado para Uruguay?"
> "Mostrame mis cuentas conectadas de Mercado Libre"

---

## Herramientas Disponibles

| Herramienta | Descripción |
|---|---|
| `search_items` | Buscar productos por palabra clave, categoría, precio, condición |
| `get_item` | Detalles completos de una publicación |
| `create_item` | Crear una nueva publicación |
| `update_item` | Actualizar precio, stock, título, descripción |
| `delete_item` | Cerrar/finalizar una publicación |
| `list_my_items` | Todas tus publicaciones, filtrables por estado |
| `relist_item` | Republicar un artículo cerrado |
| `list_categories` | Categorías principales de un sitio |
| `get_category` | Detalles y atributos de una categoría |
| `predict_category` | Mejor categoría para un título de producto |
| `search_orders` | Órdenes del vendedor, filtrables por estado |
| `get_order` | Detalles completos de una orden |
| `get_shipping_methods` | Opciones de envío para un artículo+código postal |
| `get_shipment` | Rastrear un envío |
| `list_questions` | Preguntas en tus artículos |
| `answer_question` | Responder una pregunta de un comprador |
| `get_user` | Perfil y reputación del usuario |
| `list_ads_campaigns` | Campañas de Mercado Ads |
| `get_item_visits` | Estadísticas de visitas de una publicación |
| `list_authenticated_sites` | Lista qué países tienen un token válido en caché — y cuáles no |

Todas las herramientas aceptan un parámetro opcional `site_id` que selecciona qué perfil de país autenticado ejecuta la llamada (ej. `MLA`, `MLU`). Las operaciones de lectura pública (búsqueda, categorías) pueden ser atendidas por cualquier perfil autenticado; las escrituras (`update_item`, `delete_item`, `create_item`, etc.) deben usar el perfil que realmente es dueño de la cuenta/publicación.

---

## Seguridad

- **Las credenciales nunca llegan al LLM**: Las claves se cargan desde variables de entorno o archivos `.env` y se usan solo en el proceso del servidor MCP
- **Tokens OAuth almacenados localmente, un archivo por país**: El token de acceso/actualización de cada sitio vive en su propio archivo bajo `~/.mercadolibre_mcp/profiles/<SITE_ID>.json` con permisos `chmod 600` y escrituras atómicas (un corte de luz a mitad de escritura nunca corrompe un perfil)
- **Sin bloqueos interactivos**: Si se llama a una herramienta para un país que todavía no fue autorizado, el servidor devuelve un error claro indicando qué comando `auth --site-id` ejecutar — nunca se queda esperando la autorización del navegador durante una llamada en vivo
- **Auto-actualización**: Los tokens expirados se renuevan automáticamente del lado del servidor, por perfil
- **Sin registro de credenciales**: Las credenciales nunca se escriben en los logs
- **Límites de tasa**: El servidor respeta los límites de tasa de la API de Mercado Libre con reintentos automáticos

## Estructura del Proyecto

```
mercadolibre-mcp/
├── README.md                    # Versión en inglés
├── README.es.md                 # Este archivo
├── pyproject.toml               # Configuración del proyecto Python
├── .env.example                 # Plantilla de variables de entorno
├── .gitignore
├── src/
│   └── mercadolibre_mcp/
│       ├── __init__.py
│       ├── main.py              # Servidor FastMCP + definiciones de herramientas
│       ├── client.py            # Cliente HTTP (inyección de auth, límites de tasa)
│       └── auth.py              # Gestión de tokens OAuth2
└── .venv/                       # Entorno virtual (uv)
```

Datos en tiempo de ejecución (no forman parte del repo, se crean en el primer uso):

```
~/.mercadolibre_mcp/
└── profiles/
    ├── MLA.json                 # Token de Argentina (chmod 600)
    ├── MLU.json                 # Token de Uruguay (chmod 600)
    └── ...                      # un archivo por país autorizado
```

## Licencia

MIT

## Aviso Legal

Este proyecto **no está afiliado, respaldado ni patrocinado por MercadoLibre S.R.L.** Es una integración independiente construida sobre la API REST pública de Mercado Libre. Úselo bajo su propio riesgo y en cumplimiento de los [Términos y Condiciones](https://developers.mercadolibre.com.uy/es-uy-terminos-y-condiciones) de Mercado Libre.