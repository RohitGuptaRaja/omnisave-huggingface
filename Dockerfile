# Python 3.10 का आधिकारिक और स्थिर वर्जन चुनें
FROM python:3.10-slim

# सिस्टम अपडेट करें और ffmpeg + curl-cffi के लिए ज़रूरी टूल्स इंस्टॉल करें
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    libcurl4-openssl-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face के सुरक्षा नियम के अनुसार यूज़र बनाएं
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# पहले pip को अपडेट करें ताकि curl-cffi का pre-built wheel आसानी से डाउनलोड हो सके
RUN pip install --no-cache-dir --upgrade pip

# requirements.txt कॉपी करें और इंस्टॉल करें
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# बाकी सारा प्रोजेक्ट कोड कॉपी करें
COPY --chown=user . .

# लोकल वीडियो और लॉग्स के लिए फोल्डर बनाएं
RUN mkdir -p downloads && touch omni_logs.txt

# Hugging Face के लिए पोर्ट 7860 खोलें
EXPOSE 7860

# अपना मुख्य सर्वर और बॉट चालू करें
CMD ["python", "bot.py"]
