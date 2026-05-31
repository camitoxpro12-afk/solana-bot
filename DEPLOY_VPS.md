# 🖥️ Desplegar el bot en un VPS (24/7)

Guía para correr el bot en un servidor que nunca se apaga, para que vigile tus
posiciones día y noche aunque tu PC esté apagado.

> ⚠️ **Antes de poner dinero real:** prueba en modo PAPER en tu PC primero
> (`ENABLE_TRADING=false`). El VPS es para cuando ya confíes en el bot.

---

## 1. ¿Por qué un VPS?

Tu PC se apaga, se duerme, o se corta el wifi → el bot muere y una posición
abierta se queda sin stop-loss. Un VPS (servidor virtual) está **siempre
encendido**, con conexión estable y más cerca de los servidores de Solana
(menos latencia = compras/ventas algo más rápidas).

---

## 2. Elegir y crear el VPS

Opciones baratas (suficiente: 1-2 vCPU, 2 GB RAM, Ubuntu 24.04):

| Proveedor | Precio aprox | Notas |
|-----------|--------------|-------|
| **Hetzner** (Alemania) | ~4 €/mes (CX22) | Muy buena relación calidad/precio, datacenter en EU |
| **Contabo** (Alemania) | ~5 €/mes | Mucha RAM por el precio |
| **DigitalOcean** | ~6 $/mes | Fácil de usar, mucha documentación |
| **Oracle Cloud** | Gratis (Always Free) | Capa gratuita real, pero registro más complicado |

Al crear el servidor:
1. Sistema operativo: **Ubuntu 24.04 LTS**.
2. Región: la más cercana a ti (o a EE.UU. para menor latencia con Solana).
3. Autenticación: **clave SSH** (más seguro que contraseña).

Te darán una **IP** (ej. `203.0.113.45`). La usarás para conectarte.

---

## 3. Conectarte por SSH

Desde tu PC (PowerShell en Windows ya trae `ssh`):

```bash
ssh root@203.0.113.45
```

### Crear un usuario sin privilegios (recomendado)
```bash
adduser botuser
usermod -aG sudo botuser
su - botuser
```

---

## 4. Instalar dependencias en el servidor

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git
```

---

## 5. Subir el bot al servidor

**Opción A — con git** (si subiste el proyecto a un repo PRIVADO):
```bash
git clone https://github.com/tu-usuario/solana-bot.git
cd solana-bot
```

**Opción B — copiar desde tu PC** (sin repo). Desde PowerShell en tu PC:
```powershell
scp -r "C:\Users\camit\Desktop\programa\solana-bot" botuser@203.0.113.45:/home/botuser/
```

> ⚠️ **NUNCA subas tu archivo `.env` a un repo público.** Contiene tu clave
> privada. El `.gitignore` ya lo excluye. Si usas git, crea el `.env`
> directamente en el servidor (paso 6).

---

## 6. Configurar e instalar

```bash
cd /home/botuser/solana-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Crear el .env (si no lo copiaste)
cp .env.example .env
nano .env   # pega tu PRIVATE_KEY, ANTHROPIC_API_KEY, etc. Guarda con Ctrl+O, Ctrl+X
```

**Empieza con `ENABLE_TRADING=false`** para una primera prueba en el servidor.

---

## 7. Ejecutarlo como servicio (se reinicia solo)

El archivo `solana-bot.service` ya viene incluido. Instálalo:

```bash
sudo cp /home/botuser/solana-bot/solana-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable solana-bot     # arranca solo al reiniciar el servidor
sudo systemctl start solana-bot      # arranca ahora
```

Comandos útiles:
```bash
sudo systemctl status solana-bot     # ver si está corriendo
sudo journalctl -u solana-bot -f     # ver los logs en vivo
sudo systemctl restart solana-bot    # reiniciar
sudo systemctl stop solana-bot       # parar
```

Con `Restart=always`, si el bot se cuelga, systemd lo levanta en 5 segundos. Y
con `enable`, si el servidor se reinicia, el bot vuelve solo.

---

## 8. Ver el dashboard de forma SEGURA

El servicio escucha en `127.0.0.1:8000` (solo local en el servidor), **no
expuesto a internet**. Esto es a propósito: el dashboard puede ordenar
compras/ventas, así que **nunca lo dejes abierto al público.**

Para verlo desde tu PC, abre un **túnel SSH** (reenvía el puerto de forma
cifrada):

```bash
ssh -L 8000:localhost:8000 botuser@203.0.113.45
```

Mientras ese túnel esté abierto, ve en tu navegador a **http://localhost:8000**
y verás el dashboard del servidor. Al cerrar el túnel, nadie más puede acceder.

> 🔒 **No abras el puerto 8000 en el firewall.** Si lo expones, cualquiera con
> la IP podría controlar tu bot y vaciar la wallet. El túnel SSH es la forma
> correcta.

---

## 9. Checklist de seguridad

- [ ] Wallet **dedicada** con solo el dinero que vas a arriesgar (nunca la principal).
- [ ] `.env` solo en el servidor, con permisos restringidos: `chmod 600 .env`
- [ ] Acceso al VPS por **clave SSH**, no contraseña.
- [ ] Dashboard **solo por túnel SSH**, nunca expuesto.
- [ ] Firewall básico: `sudo ufw allow OpenSSH && sudo ufw enable`
- [ ] Primera semana en `ENABLE_TRADING=false` también en el servidor.

---

## 10. Mantenimiento

- **Actualizar el bot:** `git pull` (o re-copiar) → `sudo systemctl restart solana-bot`
- **Ver cuánto gasta:** los logs muestran el coste de cada llamada a Claude.
- **Backups:** copia el archivo `bot.db` de vez en cuando (tiene tu historial y aprendizaje).

---

*Recuerda: un VPS no reduce el riesgo del trading de memecoins, solo asegura que
el bot no se apague. El riesgo de pérdida sigue siendo real.*
