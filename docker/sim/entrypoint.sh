#!/bin/bash
set -e

ISAAC_SIM=/workspace/isaaclab/_isaac_sim

export CARB_APP_PATH=$ISAAC_SIM/kit
export ISAAC_PATH=$ISAAC_SIM
export EXP_PATH=$ISAAC_SIM/apps
source ${ISAAC_SIM}/setup_python_env.sh
source /root/env 2>/dev/null

# arm64: preload libgomp or torch aborts on import (static TLS); no-op on x86-64.
if [ -z "${LD_PRELOAD:-}" ] && [ -f /usr/lib/aarch64-linux-gnu/libgomp.so.1 ]; then
  export LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libgomp.so.1
fi

cat > /usr/local/bin/python << 'WRAPPER'
#!/bin/bash
exec /workspace/isaaclab/_isaac_sim/python.sh "$@"
WRAPPER
chmod +x /usr/local/bin/python

python -m pip install -e /workspace/Sim-to-Real-SO-101-Workshop/source/sim_to_real_so101/

exec "$@"
