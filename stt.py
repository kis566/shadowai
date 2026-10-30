import speech_recognition as sr

def listen():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("\n[🎙️] Shadow sun raha hai... Boliye!")
        r.adjust_for_ambient_noise(source, duration=0.5)
        r.pause_threshold = 1.0 
        
        try:
            audio = r.listen(source, timeout=5, phrase_time_limit=15)
        except sr.WaitTimeoutError:
            print("[!] Aapne kuch nahi kaha.")
            return ""

    try:
        print("[⏳] Samajh raha hoon...")
        query = r.recognize_google(audio, language='hi-IN')
        print(f"[👤] Aapne kaha: {query}\n")
        return query

    except sr.UnknownValueError:
        print("[!] Aawaz saaf nahi aayi, kripya dobara kahein...")
        return ""
    except sr.RequestError:
        print("[!] Network Error: STT server se connect nahi ho paya.")
        return ""
    except Exception as e:
        print(f"[!] STT Error: {e}")
        return ""

if __name__ == "__main__":
    text = listen()