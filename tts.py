import edge_tts
import asyncio
import os
import platform

async def speak_text(text):
    voice = "hi-IN-MadhurNeural" 
    
    communicate = edge_tts.Communicate(text, voice)
    filename = "response_voice.mp3"
    
    await communicate.save(filename)
    
    system_os = platform.system()
    try:
        if system_os == "Windows":
            os.system(f"start {filename}")
        elif system_os == "Darwin":
            os.system(f"afplay {filename}")
        else:
            os.system(f"mpg123 {filename}") 
    except Exception as e:
        print(f"[!] Audio play karne me error aaya: {e}")

def speak(text):
    if not text:
        return
        
    print(f"[🔊] Shadow bol raha hai: {text}")
    try:
        asyncio.run(speak_text(text))
    except Exception as e:
        print(f"[!] TTS Error: {e}")

if __name__ == "__main__":
    speak("Namaste Boss, main Shadow hoon. NVIDIA combo ready hai!")