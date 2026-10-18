# Remote ASR deployment

This guide describes how to run the optional Qwen3-ASR worker on a second Windows computer. It contains placeholders only; supply the host address, file locations, model identifiers, and network rules for your own environment.

## GPU computer

1. Install a supported Python version and create a separate environment for Qwen3-ASR. Install the ASR package and a PyTorch build compatible with the computer's GPU driver.
2. Make the Qwen3-ASR and ForcedAligner model weights available to that environment. Use the model identifiers or local model directories selected for your deployment.
3. Create a strong random token and save it in a file outside the repository. Securely copy the same token file to the workbench computer. Never commit the token.
4. Start the worker on a private network interface. Replace placeholders with deployment-specific values:

   ```powershell
   python examples/asr_qwen3_server.py --host ASR_SERVER_PRIVATE_IP --port 8877 --token-file TOKEN_FILE --model ASR_MODEL --aligner ALIGNER_MODEL --device GPU_DEVICE
   ```

   Allow only the workbench computer or a trusted subnet to reach the worker port. The worker handles requests sequentially and releases model memory after each transcription.

## Workbench computer

The workbench extracts a 16 kHz mono WAV with FFmpeg and streams it to the worker. Configure the ASR command as a JSON argument array, replacing the placeholders:

```json
["python", "PATH_TO_REPOSITORY/examples/asr_remote_client.py", "--input", "{audio}", "--output", "{output}", "--url", "http://ASR_SERVER_PRIVATE_IP:8877/transcribe", "--token-file", "TOKEN_FILE"]
```

The client checks the authenticated health endpoint before uploading audio. The server returns timestamped segments in the same JSON format as the local adapters. Start with a short sample before processing longer videos.

The worker uses HTTP. Keep it inside a trusted private network or VPN, restrict firewall access, and do not publish its port to the internet. Store token files outside the repository and use separate credentials for each deployment.
