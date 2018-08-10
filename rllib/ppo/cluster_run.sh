#!/bin/bash
python main.py \
--frameskip=5 --accuracy=5e-5 \
--num-workers=80 --num-cpus=80 \
--cluster \
--num-gpus=4 \
--sample=2048  --sample-batch=16 \
--reward=standing \
--epochs=10 --hiddens=256-256 --activations=relu \
--batch-size=256 --learning-rate=5e-5 \
--seed=60730 \
--iterations=500 --checkpoint-interval=50 --validation-interval=1
