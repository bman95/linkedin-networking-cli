# LinkedIn Networking CLI - TODOs

Este archivo contiene la lista de features, mejoras y detalles pendientes para futuras versiones.

---

## 🚀 Próximas Features

### Filtros Avanzados de Búsqueda

#### 1. Current Company Filter
**Prioridad:** Alta
**Descripción:** Permitir buscar personas que trabajan en empresas específicas.

**Implementación pendiente:**
- Agregar campo `current_company_ids` al modelo Campaign
- Crear UI para búsqueda de empresas
- Opciones de implementación:
  - **A) Input manual:** Usuario busca en LinkedIn y copia el ID de empresa del URL
  - **B) Búsqueda integrada:** Usar typeahead API de LinkedIn para buscar empresas
  - **C) Archivo de configuración:** Mantener lista de empresas comunes pre-configuradas

**Formato URL:** `currentCompany=["1441","1586","1035"]` (múltiples IDs)

**Ejemplo de uso:**
- Buscar empleados de Google, Meta, Apple
- IDs: Google="1441", Meta="1586", Apple="162479"

---

#### 2. School/University Filter
**Prioridad:** Media
**Descripción:** Filtrar por alumni de universidades específicas.

**Implementación pendiente:**
- Agregar campo `school_ids` al modelo Campaign
- Crear UI para selección de universidades
- Formato: `schoolFilter=["166622","1792","12297"]`

**Use cases:**
- Recruiting de alumni de Stanford, MIT, Harvard
- Networking con personas de misma universidad

---

#### 3. Past Company Filter
**Prioridad:** Media
**Descripción:** Buscar personas que trabajaron anteriormente en ciertas empresas.

**Implementación pendiente:**
- Agregar campo `past_company_ids` al modelo Campaign
- Similar a current_company pero para empleos anteriores
- Formato: `pastCompany=["1035","1441","1586"]`

**Use cases:**
- Ex-empleados de FAANG companies
- Alumni de startups específicas

---

#### 4. Profile Language Filter
**Prioridad:** Baja
**Descripción:** Filtrar perfiles por idioma.

**Implementación pendiente:**
- Agregar campo `profile_language` al modelo Campaign
- Simple dropdown: English, Spanish, French, German, etc.
- Formato: `profileLanguage=["en"]`

**Mapping:**
```python
LANGUAGE_OPTIONS = {
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Portuguese": "pt",
    "Chinese": "zh",
    "Japanese": "ja",
}
```

---

#### 5. Service Category Filter
**Prioridad:** Baja
**Descripción:** Filtrar por categorías de servicio (para freelancers/consultores).

**Implementación pendiente:**
- Agregar campo `service_category_ids` al modelo Campaign
- Formato: `serviceCategory=["220"]`
- Investigar IDs de categorías disponibles

---

#### 6. Follower Of Filter
**Prioridad:** Baja
**Descripción:** Buscar seguidores de un perfil específico.

**Implementación pendiente:**
- Agregar campo `follower_of_urn` al modelo Campaign
- Formato: `followerOf=["ACoAACAX-bAB9B1BSm-SWFPe6HY5wzjtcMO06gE"]`
- Necesita URN del perfil objetivo

---

## 🔍 Investigación Necesaria

### LinkedIn Location IDs (geoUrn)

**Status:** ✅ IMPLEMENTADO - Solución Híbrida

**Implementación actual:**
- ✅ Lista curada con ~75+ ciudades importantes de todo el mundo
- ✅ Opción "Other" para ingresar códigos personalizados manualmente
- ✅ Función `LinkedInAutomation.search_location()` para búsqueda dinámica (backend)

**Códigos verificados:**
- San Francisco Bay Area: `90000084` ✅
- Greater Boston Area: `105646813` ✅
- United States: `103644278` ✅

**Códigos en lista (pendientes de verificación manual):**
- ~75 ciudades importantes en US, Canadá, Europa, Asia-Pacífico, Latinoamérica
- Ver lista completa en `src/automation/linkedin_mappings.py`

**Cómo usar:**
1. **Opción A - Lista curada**: Seleccionar de la lista expandida en UI
2. **Opción B - Manual**: Seleccionar "Other" e ingresar geoUrn personalizado
3. **Opción C - Dinámica (futuro)**: Búsqueda en tiempo real via Voyager API

---

### 🔮 Búsqueda Dinámica de Ubicaciones (Fase Futura)

**Status:** Backend implementado, UI pendiente
**Prioridad:** Media

**Backend ya disponible:**
```python
# En src/automation/linkedin.py
async def search_location(query: str) -> List[Dict[str, str]]:
    """Búsqueda dinámica usando Voyager API"""
    # Returns: [{"name": "San Francisco Bay Area", "geoUrn": "90000084"}]
```

**Implementación pendiente:**
- Hacer la UI de creación de campaña async
- Agregar búsqueda con autocompletado en tiempo real
- Requerir autenticación previa antes de crear campaña

**API Endpoint usado:**
```
GET /voyager/api/typeahead/hitsV2?
    keywords={query}&
    origin=OTHER&
    q=type&
    queryContext=List(geoVersion->3,bingGeoSubTypeFilters->MARKET_AREA|COUNTRY_REGION|ADMIN_DIVISION_1|CITY)&
    type=GEO
```

**Ventajas:**
- Acceso a TODAS las ubicaciones de LinkedIn (no solo lista curada)
- Datos siempre actualizados
- Búsqueda typo-tolerant

**Desventajas:**
- Requiere sesión autenticada
- UI más compleja
- Latencia de red

**Decisión de implementación futura:** Agregar como modo avanzado opcional

---

### LinkedIn Industry IDs

**Status:** Investigación inicial
**Códigos parciales:**
- Computer Software: `1594` ✅
- Internet: `6` ✅
- Technology: `4` ❓

**Pendiente:**
- Obtener lista completa de IDs de industrias
- Verificar cada código con búsquedas reales
- Recursos: LinkedIn API docs, reverse engineering de búsquedas

---

### LinkedIn Company/School IDs

**Status:** Sin implementar

**Cómo obtener IDs:**
1. **Manual:**
   - Buscar empresa/universidad en LinkedIn
   - Ver URL del perfil: `/company/1441/` → ID es `1441`

2. **Programático:**
   - LinkedIn Typeahead API: `/voyager/api/typeahead/...`
   - Requiere autenticación
   - Implementar helper function

**Empresas comunes a pre-configurar:**
```
Google: 1441
Meta: 1586
Apple: 162479
Microsoft: 1035
Amazon: 1586
Netflix: 165158
Tesla: 15564
```

---

## 🎨 UX/UI Mejoras

### Campaign Creation Flow

**Opción 1: Flow lineal (actual)**
- Simple, paso a paso
- ✅ Fácil de entender
- ❌ Puede ser largo con filtros avanzados

**Opción 2: Flow con secciones**
```
1. Basic Info (name, description)
2. Core Filters (keywords, location, industry, network)
3. Advanced Filters? (Yes/No)
   └─ Si Yes → mostrar filtros avanzados
4. Settings (daily limit, message)
5. Review & Create
```

**Opción 3: Interactive form**
- Un solo formulario con todas las opciones
- Filtros avanzados colapsados
- Más rápido pero más complejo

**Decisión pendiente:** Feedback del usuario

---

### Campaign Editing

**Status:** ✅ IMPLEMENTADO (CRUD básico)
**Prioridad:** Alta

Implementado en "Manage Campaigns" (cableado a SQLite vía `DatabaseManager`):
- ✅ Editar nombre, descripción, daily limit y message template
- ✅ Pausar/reanudar campañas (toggle active/inactive)
- ✅ Eliminar campaña y sus contactos asociados

**Pendiente (futuro):**
- Editar filtros de targeting (keywords/location/industry) sin recrear la campaña

---

### Campaign Templates

**Status:** Idea
**Prioridad:** Media

Guardar configuraciones comunes como templates:
- "FAANG Recruiters"
- "Local Startup Founders"
- "Remote Developers"
- "Sales Professionals in SF"

---

## 🐛 Bugs Conocidos

### 1. Location Filter No Funciona
**Status:** ✅ RESUELTO en Fase 1
**Problema:** Enviaba texto en lugar de geoUrn numérico
**Solución:** Implementado mapeo de ubicaciones

---

### 2. Search URL Incorrecto
**Status:** ✅ RESUELTO en Fase 1
**Problema:** Usaba `/search/people/` en lugar de `/search/results/people/`
**Solución:** Corregido en linkedin.py

---

## 📊 Analytics & Reporting

### Dashboard Mejorado
- Gráficos de acceptance rate por campaña
- Timeline de conexiones enviadas
- Heatmap de mejores días/horas
- A/B testing de message templates

### Export Features
- Exportar contactos a CSV
- Integración con CRM (HubSpot, Salesforce)
- Webhook notifications

---

## 🔒 Seguridad & Rate Limiting

### LinkedIn Detection Avoidance
- Random delays entre acciones (✅ ya implementado)
- Respetar daily limits (✅ ya implementado)
- Human-like browsing patterns
- Session management (✅ ya implementado)

### Mejoras pendientes:
- Detectar CAPTCHAs y pausar
- Notificaciones si cuenta está restringida
- Backoff automático si hay errores

---

## 🧪 Testing

### Unit Tests
- Tests para URL builder
- Tests para mappings
- Tests para database operations

### Integration Tests
- End-to-end campaign flow
- LinkedIn authentication
- Error handling

### Manual Testing Checklist
- [ ] Crear campaña con todos los filtros
- [ ] Verificar URL generada
- [ ] Ejecutar búsqueda real en LinkedIn
- [ ] Verificar resultados match con filtros
- [ ] Probar con diferentes combinaciones

---

## 📝 Documentación

### User Guide
- Guía de inicio rápido
- Cómo encontrar geoUrn codes
- Cómo encontrar company/school IDs
- Best practices para mensajes
- Tips para evitar restricciones

### Developer Guide
- Arquitectura del proyecto
- Cómo agregar nuevos filtros
- Cómo extender mappings
- API de LinkedIn (no oficial)

---

## 🌐 Internacionalización

### Multi-language Support
- UI en español
- UI en otros idiomas
- Message templates localizados

---

## 🚀 Performance

### Optimizaciones
- Caché de búsquedas
- Batch processing de conexiones
- Async operations (✅ ya implementado)
- Database indexing (✅ ya implementado)

---

## 📦 Deployment

### Packaging
- PyPI package
- Docker container
- Executables standalone (PyInstaller)

### Distribution
- Homebrew formula (macOS)
- APT/DNF packages (Linux)
- Chocolatey package (Windows)

---

**Última actualización:** 2026-06-10
**Versión actual:** 0.1.0
**Próxima versión planeada:** 0.2.0 (Fase 1 completada)

---

## ✅ Completado en esta iteración

- Cableado de "Manage Campaigns" a la base de datos: toggle activar/desactivar, editar y eliminar
  campaña — antes eran stubs que solo imprimían "In real app: Would...".
- `edit_campaign` ahora también edita los **filtros de targeting** (keywords, ubicación,
  industria, grado de conexión), no solo nombre/límite/plantilla.
- Nuevo **export de contactos a CSV** por campaña (Manage Campaigns → "Export contacts to CSV").
- Nueva utilidad **"Look up location code (online)"** en Settings: usa la Voyager API
  (`search_location`) para encontrar geoUrn de cualquier ubicación, autenticando la sesión.
- **Conversión async crítica:** `interactions.py` y `scraping.py` estaban escritos en estilo
  síncrono sobre una página async, así que la detección de CAPTCHA/límites y la extracción de
  perfiles/contactos nunca funcionaban. Convertidos a async/await y actualizados los call sites
  en `linkedin.py` y `checker.py`.
- Corregido `record_daily_analytics`: `DetachedInstanceError` al actualizar (faltaba `refresh`).
- Corregidos IDs duplicados en mappings: `LOCATION_CHOICES` (Atlanta/Phoenix) e `INDUSTRY_CHOICES`
  (Design).
- Actualizados los tests de login al esquema de detección por URL.
- Añadidos tests para `interactions` y `scraping` (cobertura de esos módulos 15%/0% → 46%/31%).
- Añadidos **CI** (GitHub Actions: `uv sync` + `pytest`), **Dockerfile** y `.dockerignore`.
- README reescrito (sin mojibake, con disclaimer de ToS, export CSV, Docker, testing).
- Eliminado el "File Editor Demo" (andamiaje ajeno al producto).
- Suite de tests en verde: **269 passed**.

### Completado (segunda tanda de pendientes)
- **Búsqueda dinámica de ubicaciones integrada en el flujo de creación Y edición** de campañas
  (opción "🔎 Search location online"), además de la utilidad en Settings. Helper compartido
  `_run_location_search` / `_search_location_online`.
- **Backoff exponencial ante cuenta restringida** en `send_connection_requests`: se detiene ante
  CAPTCHA o límite semanal de invitaciones y aplica backoff exponencial (5s→…→300s) tras fallos
  consecutivos.
- **Cobertura subida**: `session.py` 0%→97%, `checker.py` 0%→41% (tests nuevos). Total 49%→58%,
  **294 tests**.
- **Packaging a PyPI**: añadido `[build-system]` (hatchling) + metadata (license, classifiers,
  keywords, urls); `uv build` genera wheel/sdist y el wheel instala e importa correctamente en un
  entorno Python 3.13 limpio (entry point `linkedin-cli` funcional).

### Pendiente real (requiere cuenta de LinkedIn / fuera de este entorno)
- Verificar los geoUrn/industry IDs marcados con `❓` contra búsquedas reales (mitigado: usar el
  lookup online integrado o geoUrn custom; los `❓` están claramente marcados como no verificados).
- Tests de integración end-to-end contra LinkedIn real (login, search, send) — requieren credenciales
  y navegador; el resto de la lógica ya está cubierta con mocks.
