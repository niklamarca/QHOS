#!/bin/bash
# ============================================================
# QUANT HPC OS - Setup Script
# ============================================================

set -e
echo "============================================"
echo "  QUANT HPC OS — Setup"
echo "============================================"

# 1. Python environment
echo "[1/6] Criando ambiente virtual..."
python3 -m venv .venv
source .venv/bin/activate

# 2. Upgrade pip
pip install --upgrade pip wheel setuptools

# 3. Install PyTorch with CUDA (RTX 5090 → CUDA 12.4+)
echo "[2/6] Instalando PyTorch com suporte CUDA..."
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# 4. Install all dependencies
echo "[3/6] Instalando dependências do sistema..."
pip install -r requirements.txt

# 5. Create .env file if not exists
echo "[4/6] Configurando variáveis de ambiente..."
if [ ! -f .env ]; then
cat > .env << 'EOF'
# ============================================================
# QUANT HPC OS - Environment Variables
# ============================================================

# LLM
ANTHROPIC_API_KEY=your_anthropic_key_here

# Brokers
ALPACA_API_KEY=your_alpaca_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets

BINANCE_API_KEY=your_binance_key_here
BINANCE_SECRET_KEY=your_binance_secret_here

IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1

# System
SYSTEM_MODE=research          # research | paper | live
LOG_LEVEL=INFO
DASHBOARD_PORT=8000
MAX_CPU_THREADS=32
GPU_DEVICE=cuda:0

# Risk Limits
MAX_PORTFOLIO_DRAWDOWN=0.10   # 10%
MAX_POSITION_SIZE=0.05        # 5% por ativo
MAX_DAILY_LOSS=0.02           # 2% perda diária
EOF
echo "  → .env criado. Configure suas chaves antes de iniciar."
fi

# 6. Validate GPU
echo "[5/6] Verificando GPU..."
python3 -c "
import torch
if torch.cuda.is_available():
    print(f'  GPU: {torch.cuda.get_device_name(0)}')
    print(f'  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB')
else:
    print('  Aviso: GPU/CUDA não disponível — regime model rodará em CPU')
"

# 7. Init data lake structure
echo "[6/6] Inicializando Data Lake..."
python3 -c "
from data.storage import StorageEngine
StorageEngine.initialize()
print('  Data Lake inicializado com sucesso.')
"

echo ""
echo "============================================"
echo "  Setup concluído!"
echo "  Execute: python main.py"
echo "============================================"
