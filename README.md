# ChartView

Aplicación de escritorio en Python para graficar acciones de México (BMV), EE.UU. y criptomonedas, con más de 70 indicadores técnicos. Corre localmente; los datos se descargan de Yahoo Finance.

## Ejecutar

Requiere Python 3.10 o más reciente. Desde la carpeta del proyecto, la primera vez:

```
python -m venv .venv
```

Windows:

```
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python src\main.py
```

Linux / macOS:

```
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python src/main.py
```

Las siguientes veces basta con la última línea.

## Símbolos

| Mercado | Escribes | Se abre |
|---|---|---|
| México (BMV) | `WALMEX`, `AMXB`, `GFNORTEO` | `WALMEX.MX`… · IPC: `^MXX` · Dólar: `MXN=X` |
| EE.UU. | `AAPL`, `SPY`, `^GSPC` | igual |
| Asia | `^N225` (Nikkei), `^HSI` (Hang Seng), `^KS11` (KOSPI), `000001.SS` (Shanghai), `^TWII`… | igual · acciones: `7203.T` Tokio, `0700.HK` Hong Kong, `600519.SS` China, `005930.KS` Corea |
| Macro EE.UU. | `M2`, `M1`, `FED_BALANCE`, `FED_FUNDS`, `UST3M`, `UST2Y`, `UST10Y`, `UST30Y`, `SPREAD_10Y2Y`, `SPREAD_10Y3M` | `MACRO:M2`… (datos oficiales de la Reserva Federal vía DBnomics) · bonos en Yahoo: `2YY=F`, `^TNX`, `ZT=F`, `ZN=F`, `TLT` · con clave de FRED, cualquier ID: `WALCL`, `M2SL`, `DGS10`… |
| Cripto | `BTC`, `ETH`, `SOL` | `BTC-USD`… (también `BTC-EUR`, `ETH-BTC`) |
| Directo | cualquier símbolo de Yahoo | igual (`GC=F`, `EURUSD=X`, `^N225`…) |

Con **Buscar** (Ctrl+F) puedes buscar por nombre ("Cemex", "Bitcoin", "bono 10", "balance").

### Datos macro

- **Catálogo de la Fed** (sin registro): M2 y M1 (H.6, mensual desde 1959), balance de la Fed (H.4.1, semanal desde 2002), tasas del Tesoro y fondos federales (H.15, diario) y los diferenciales de la curva.
- **FRED**: en ⚙ → *Clave API de FRED* escribes una clave gratuita (https://fredaccount.stlouisfed.org/apikeys). Con ella abres cualquier serie de FRED por su ID en el mercado "Macro EE.UU.".
- **Series propias / ISM PMI**: el ISM PMI no se publica en ninguna fuente gratuita (FRED lo retiró en 2016). Guarda el histórico en un CSV con dos columnas, fecha y valor, e impórtalo en ⚙ → *Importar serie desde CSV*. Queda como `CSV:<NOMBRE>`. Para actualizarlo, vuelve a importar el CSV con el mismo nombre.
- Las series con un solo valor por fecha se dibujan como línea. Con un intervalo más amplio que la serie (p. ej. tasas diarias en 1 semana) se arman velas.
- Para ver el crecimiento anual de M2, agrega el indicador ROC con periodo 12 en intervalo mensual.

## Uso

- Rueda del mouse: zoom en el tiempo · arrastrar: desplazar · el eje Y se ajusta solo.
- **Indicadores** (Ctrl+I): catálogo por categorías, parámetros editables, color y orden. La flecha del botón agrega un indicador directo con valores por defecto.
- Tipos de gráfica: velas, Heikin Ashi, barras OHLC, línea y área.
- Casilla **Log** (Alt+L): escala logarítmica en la gráfica de precio, incluidos los indicadores dibujados sobre ella.
- Intervalos de 1 minuto a 12 meses. Las velas de 4 horas se arman con datos de 1 hora, y las de 2 semanas con velas semanales (siempre empiezan en lunes). Las de 3, 6 y 12 meses se arman con velas mensuales, alineadas a trimestres, semestres y años del calendario.
- El panel de listas se puede cerrar con la ✕ o arrastrar a otro lado. Se vuelve a abrir con **☰ Listas** (primer botón de la barra) o Ctrl+B. La app recuerda dónde lo dejaste.
- Varias listas de seguimiento: el menú ⋯ crea, duplica, renombra y elimina listas, y con clic derecho copias un símbolo a otra lista.
- **Dibujo** (barra vertical a la izquierda de la gráfica):
  - Herramientas: línea horizontal, línea vertical, línea de tendencia, rayo, rectángulo, círculo/elipse, dibujo libre, texto, imagen (PNG/JPG…), Fibonacci manual y borrador.
  - Con el cursor (↖) puedes mover y redimensionar lo dibujado. Clic derecho sobre un dibujo cambia el color o el grosor, o lo elimina.
  - Esc cancela la herramienta, Ctrl+Z deshace, 👁 oculta los dibujos y 🗑 los borra todos.
  - Se guardan por símbolo, anclados a fecha y precio, así que siguen en su lugar al cambiar el intervalo, el periodo o la escala log.
  - Las imágenes se copian a `%USERPROFILE%\.chartview\images`.
- **Alertas** (🔔 en la barra o Ctrl+Alt+A):
  - Condiciones: el precio cruza un nivel o queda por encima/debajo de él; el cambio del día sube o cae más de X %; el RSI cruza un umbral; el precio cruza una SMA/EMA en el intervalo que elijas.
  - Funcionan con acciones, índices, cripto y series macro.
  - Avisan con una notificación de escritorio, un sonido y parpadeo en la barra de tareas. Al hacer clic en la notificación se abre la gráfica.
  - Se revisan cada 30 s a 15 min. "Una vez" desactiva la alerta al dispararse; "Cada vez" la vuelve a armar cuando la condición deja de cumplirse.
  - Las de cruce solo avisan cuando el cruce ocurre. Las de nivel avisan también si ya se cumplen al crearlas.
  - Las alertas de precio del símbolo abierto se ven como líneas naranjas 🔔. Con clic derecho sobre una línea horizontal dibujada eliges "Crear alerta en este precio".
  - Opcional: al cerrar la ventana, ChartView sigue vigilando desde la bandeja del sistema. Se cierra del todo con clic derecho en su icono → Salir.
  - Las alertas solo se revisan mientras ChartView está abierto (o en la bandeja).
- Actualización automática, lista de seguimiento con precios y captura PNG (Ctrl+S).
- F5 actualiza · Inicio vuelve a las velas recientes.

La configuración se guarda en `%USERPROFILE%\.chartview\config.json`.

## Indicadores

- **Medias móviles:** SMA, EMA, WMA, HMA, DEMA, TEMA, VWMA, KAMA, ALMA, LSMA, McGinley, cinta de EMAs.
- **Bandas y canales:** Bollinger, Keltner, Donchian, Envolventes, Ichimoku, Pivotes.
- **Tendencia:** Parabolic SAR, Supertrend, Chandelier Exit, ZigZag, Fractales, ADX/DMI, Aroon, Oscilador Aroon, Vortex.
- **Osciladores:** RSI, MACD, Estocástico, StochRSI, CCI, Williams %R, ROC, Momentum, TRIX, Awesome, Ultimate, PPO, TSI, Elder Ray, DPO, Coppock, Fisher, KST, CMO, RVGI, BOP, Squeeze Momentum, Schaff Trend Cycle, Connors RSI.
- **Volumen:** Volumen, VWAP, OBV, MFI, CMF, A/D, Chaikin Oscillator, Force Index, EOM, PVT, Klinger.
- **Fibonacci y Gann:** Retroceso de Fibonacci automático (con extensiones), Zonas de tiempo de Fibonacci, Bandas de Fibonacci, Abanico de Gann, Cuadrado de 9 de Gann, Gann HiLo Activator. Se calculan sobre el máximo y el mínimo de las últimas N velas (parámetro «Velas a analizar»).
- **Volatilidad:** ATR, NATR, Desviación estándar, Volatilidad histórica, %B, Ancho de Bollinger, Choppiness, Mass Index, Ulcer Index.

Para agregar uno nuevo, decora una función en `src/indicators.py` con `@indicator(...)`. Aparece solo en los menús.

## Notas

- Yahoo limita la historia intradía: 1 min ≈ 7 días, 2–30 min ≈ 60 días, 1 hora ≈ 2 años. La app ajusta el periodo automáticamente.
- Los datos de la BMV en Yahoo pueden tener un retraso de unos 15–20 minutos.
