# Nemotron Transcribe Web UI

หน้าเว็บสำหรับถอดเสียงด้วย **NVIDIA Nemotron 3.5 Transcribe** บนเครื่องตัวเอง
clone ลงเครื่องแล้วดับเบิลคลิก `start.bat` ไฟล์เดียว ระบบจะติดตั้งทุกอย่าง รันโมเดล
แล้วเปิดหน้าเว็บที่ **http://localhost:8090** ให้เอง

![screenshot](docs/screenshot.png)

## ความสามารถ

- อัปโหลดหรือลากไฟล์เสียง/วิดีโอมาวาง (mp3, wav, m4a, flac, mp4 …) ยาวสูงสุดประมาณ 1 ชั่วโมง หรืออัดจากไมค์
- เลือกงาน: ถอดเสียง / แยกผู้พูด / มีเวลากำกับ / สรุปเนื้อหา / ถามคำถามเกี่ยวกับเสียง — แก้ prompt เองได้
- ข้อความขึ้นแบบ real-time พร้อมบอกความเร็วเทียบเวลาจริง
- คัดลอก / บันทึกเป็น `.txt` / เก็บประวัติในเบราว์เซอร์ / โหมดมืด / ใช้บนมือถือในวง LAN ได้
- ทุกอย่างรันในเครื่อง ไฟล์เสียงไม่ถูกส่งออกนอกเครื่อง
- ใช้พอร์ต 8090 จึงเปิดพร้อม Open WebUI (8080) ได้

## สิ่งที่ต้องมี

| | |
|---|---|
| ระบบ | Windows 10/11 ที่มี **WSL2 + Ubuntu** หรือ Linux |
| การ์ดจอ | NVIDIA, VRAM แนะนำ 32 GB ขึ้นไป — Blackwell (RTX 50xx, RTX PRO 6000) ใช้รุ่น NVFP4, รุ่นเก่ากว่าระบบจะเลือก FP8/BF16 ให้ (ใช้ VRAM มากกว่า) |
| พื้นที่ดิสก์ | ประมาณ 40 GB ขึ้นไป (vLLM + ตัวโมเดล) |
| โปรแกรม | [Git](https://git-scm.com/download/win), [Python 3.10+](https://www.python.org/downloads/) (ติ๊ก *Add python.exe to PATH*), ไดรเวอร์ NVIDIA ล่าสุด |

ถ้ายังไม่มี WSL ให้เปิด PowerShell แบบ Administrator แล้วรันครั้งเดียว จากนั้นรีสตาร์ทเครื่อง:

```powershell
wsl --install -d Ubuntu
```

## เริ่มใช้งาน

```bat
git clone https://github.com/Tumzzz000/nemotron-transcribe-webui.git
cd nemotron-transcribe-webui
start.bat
```

หรือดับเบิลคลิก `start.bat` ใน File Explorer ก็ได้

`start.bat` จะทำตามลำดับ:

1. สร้าง Python environment และติดตั้งแพ็กเกจของหน้าเว็บ (ครั้งแรกครั้งเดียว)
2. ถ้ายังไม่มี vLLM server ทำงานอยู่ จะเปิดหน้าต่างใหม่แล้วรันใน WSL:
   - **ครั้งแรก:** ติดตั้ง vLLM 0.32.0 + NeMo Speech และดาวน์โหลดโมเดล (~22 GB สำหรับ NVFP4) — ใช้เวลา 30 นาทีขึ้นไปขึ้นกับเน็ต
     ระหว่างติดตั้งอาจถามรหัสผ่าน Ubuntu (เพื่อติดตั้ง git/curl)
   - **ครั้งต่อไป:** โหลดโมเดลเข้าการ์ดจอ ใช้เวลาไม่กี่นาที
3. เปิดหน้าเว็บ http://localhost:8090 — ไฟมุมขวาบนจะเป็นสีเขียวเมื่อโมเดลพร้อม (หน้าเว็บตรวจให้อัตโนมัติทุก 5 วินาที)

ปิดใช้งาน: ปิดหน้าต่าง **Nemotron vLLM server** และหน้าต่าง **Nemotron Transcribe**

อัปเดตเวอร์ชันใหม่:

```bat
git pull
```

## การตั้งค่า

คัดลอก `config.example.bat` เป็น `config.local.bat` แล้วแก้ค่าในนั้น (ไฟล์นี้ไม่ถูก git track จึงไม่หายตอน `git pull`)

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `WEBUI_PORT` | `8090` | พอร์ตของหน้าเว็บ |
| `VLLM_URL` | `http://localhost:8000` | ที่อยู่ vLLM server |
| `START_SERVER` | `1` | `0` = เปิดแค่หน้าเว็บ ใช้กับ vLLM server ที่รันอยู่เครื่องอื่น |
| `MODEL_VARIANT` | ว่าง (เลือกตามการ์ดจอ) | บังคับใช้ `NVFP4`, `FP8` หรือ `BF16` |
| `EXTRA_VLLM_ARGS` | ว่าง | flag เพิ่มเติมของ `vllm serve` |
| `HF_TOKEN` | — | token ของ Hugging Face ถ้าการดาวน์โหลดขอ |
| `HOST` | `127.0.0.1` | ตั้ง `0.0.0.0` เพื่อให้มือถือ/เครื่องอื่นในวง LAN เปิดหน้าเว็บได้ |

ในหน้าเว็บ (ปุ่มฟันเฟือง) ตั้ง temperature, max tokens, system prompt และดู reasoning ของโมเดลได้

## Linux

```bash
./server/start-vllm.sh   # หน้าต่างที่ 1: ติดตั้ง (ครั้งแรก) และรันโมเดล
./start.sh               # หน้าต่างที่ 2: หน้าเว็บที่ http://localhost:8090
```

## โครงสร้างโปรเจกต์

```
start.bat              ตัวเริ่มทุกอย่างบน Windows
start.sh               เปิดหน้าเว็บบน Linux/macOS
config.example.bat     ตัวอย่างการตั้งค่า
app.py                 web server (FastAPI) — แปลงเสียงเป็น WAV 16 kHz แล้วส่งต่อให้ vLLM
static/index.html      หน้าเว็บ
server/setup.sh        ติดตั้ง vLLM + NeMo Speech ใน ~/.nemotron-transcribe
server/start-vllm.sh   เลือก checkpoint ตามการ์ดจอ ดาวน์โหลด และรัน vllm serve
```

## ข้อจำกัดที่ควรรู้

- คำสั่งติดตั้งและ flag ของ `vllm serve` อ้างอิงจากข้อมูลที่ NVIDIA ประกาศตอนเปิดตัว (ต.ค. 2026)
  ถ้าเจอ error ให้เทียบกับหัวข้อ Environment Setup / Quick Start ใน
  [model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-NVFP4)
  และใส่ flag ที่ขาด (เช่น MTP speculative decoding, timestamp middleware) ใน `EXTRA_VLLM_ARGS`
- Prompt สำเร็จรูปเป็นค่าเริ่มต้นทั่วไป รูปแบบ prompt ที่โมเดลเข้าใจดีที่สุดดูได้จาก model card
  แก้ได้ที่ `PRESETS` ใน `static/index.html`
- ยังไม่ได้ยืนยันว่าโมเดลรองรับภาษาไทยในระดับไหน
- Kernel แบบ quantized บางตัวอาจต้องใช้ CUDA 13 toolkit (`nvcc`) ใน WSL ตามที่ model card ระบุ

## เครดิต

โมเดล [NVIDIA Nemotron 3.5 Transcribe](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B)
อยู่ภายใต้ OpenMDW-1.1 license ของ NVIDIA — โปรเจกต์นี้เป็นแค่หน้าเว็บสำหรับเรียกใช้ ไม่ได้มาจาก NVIDIA
