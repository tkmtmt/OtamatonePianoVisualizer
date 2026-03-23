import sounddevice as sd
import numpy as np
import matplotlib.pyplot as plt

DEVICE_INDEX = 2       # MOTU M4 In 1-2 のデバイス番号
DURATION = 2           # 録音時間（秒）
SAMPLERATE = 44100     # サンプリングレート

# 録音
print("録音中...")
recording = sd.rec(int(DURATION * SAMPLERATE),
                   samplerate=SAMPLERATE,
                   channels=1,
                   dtype='float32',
                   device=DEVICE_INDEX)
sd.wait()
print("録音完了")

# 波形表示
plt.figure()
plt.plot(recording)
plt.title("オタマトーンの波形")
plt.xlabel("サンプル")
plt.ylabel("振幅")

# FFT
signal = recording[:, 0]
N = len(signal)
fft = np.fft.rfft(signal * np.hanning(N))  # 窓関数をかける
freqs = np.fft.rfftfreq(N, 1 / SAMPLERATE)
magnitude = 20 * np.log10(np.abs(fft) + 1e-10)  # dBスケール（+1e-10でlog(0)防止）

# FFT表示
plt.figure()
plt.plot(freqs, magnitude)
plt.title("FFTスペクトル")
plt.xlabel("周波数 [Hz]")
plt.ylabel("レベル [dB]")
plt.xlim(0, 2000)  # 人間の声や楽器の主成分が集中する範囲
plt.grid(True)

plt.show()
