
import { useRef, useState } from "react";
import "./App.css";
function App() {
  const [loading, setLoading] = useState(false);
  const [ticket, setTicket] = useState(null);
  const [language, setLanguage] = useState("te");
  const [phone, setPhone] = useState("");
  const [recording, setRecording] = useState(false);
  const [audioUrl, setAudioUrl] = useState("");
  const [photo, setPhoto] = useState(null);
  const [message, setMessage] = useState("");
  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });

      streamRef.current = stream;
      chunksRef.current = [];

      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        const audioBlob = new Blob(chunksRef.current, {
          type: recorder.mimeType,
        });

        setAudioUrl(URL.createObjectURL(audioBlob));
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      };

      recorder.start();
      setRecording(true);
      setMessage("");
    } catch (error) {
      setMessage(
        "Microphone access failed. Please allow microphone permission or try another browser."
      );
    }
  }

  function stopRecording() {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
      setRecording(false);
    }
  }



  async function handleSubmit(event) {
    event.preventDefault();

    if (loading) {
      return;
    }

    if (!/^[0-9]{10}$/.test(phone)) {
      setMessage("Please enter a valid 10-digit phone number.");
      return;
    }

    if (!audioUrl) {
      setMessage("Please record your complaint before submitting.");
      return;
    }

    setLoading(true);
    setMessage("");
    setTicket(null);

    try {

      const audioBlob = await fetch(audioUrl).then((response) =>
        response.blob()
      );

      const formData = new FormData();

      formData.append("customer_phone", phone);
      formData.append("language", language);
      formData.append(
        "audio",
        audioBlob,
        `complaint.${audioBlob.type.includes("webm") ? "webm" : "audio"}`
      );

      if (photo) {
        formData.append("photo", photo);
      }

      const response = await fetch(
        "http://127.0.0.1:8000/api/complaints/",
        {
          method: "POST",
          body: formData,
        }
      );


      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.error || "Could not submit your complaint."
        );
      }

      setTicket(data);
      setMessage("Your complaint has been submitted successfully!");

      // Clear the form fields after successful submission.
      setPhone("");
      setLanguage("te");
      setPhoto(null);

      // Release the old audio preview and clear it.
      if (audioUrl) {
        URL.revokeObjectURL(audioUrl);
      }
      setAudioUrl("");

      // Reset the file input visually.
      const photoInput = document.getElementById("photo");
      if (photoInput) {
        photoInput.value = "";
      }
    } catch (error) {
      setMessage(
        error.message ||
        "Unable to connect to the server. Please try again."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#home">
          <span className="brand-icon">V</span>
          VoiceDesk
        </a>
        <span className="header-label">Customer Portal</span>
      </header>

      <main className="page-content" id="home">
        <section className="intro">
          <span className="eyebrow">WE'RE HERE TO HELP</span>
          <h1>Tell us what happened.</h1>
          <p>
            Speak in your preferred language. We'll help you turn your
            complaint into a ticket our support team can understand.
          </p>
        </section>

        <form className="complaint-card" onSubmit={handleSubmit}>
          <div className="card-heading">
            <div>
              <h2>Submit a complaint</h2>
              <p>Fill in your details and record your voice message.</p>
            </div>
            <span className="step-label">STEP 1 OF 3</span>
          </div>

          <div className="field">
            <label htmlFor="language">Preferred language</label>
            <select
              id="language"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
            >
              <option value="te">Telugu</option>
              <option value="hi">Hindi</option>
              <option value="en">English</option>
              <option value="ta">Tamil</option>
              <option value="kn">Kannada</option>
            </select>
            <span className="field-help">
              Choose the language you'll speak in.
            </span>
          </div>

          <div className="field">
            <label htmlFor="phone">Mobile number</label>
            <input
              id="phone"
              type="tel"
              inputMode="numeric"
              autoComplete="tel-national"
              placeholder="Enter your 10-digit number"
              value={phone}
              maxLength={10}
              onChange={(event) =>
                setPhone(event.target.value.replace(/\D/g, ""))
              }
            />
            <span className="field-help">
              Phone verification will be added in a later step.
            </span>
          </div>

          <div className="field">
            <label>Voice complaint</label>
            <div className={`record-box ${recording ? "is-recording" : ""}`}>
              <div className="mic-icon">
                {recording ? "●" : "🎙"}
              </div>
              <h3>{recording ? "Recording in progress" : "Your voice matters"}</h3>
              <p>
                {recording
                  ? "Speak clearly. Press stop when you're finished."
                  : "Tap the button and explain your complaint in your language."}
              </p>

              {!recording ? (
                <button
                  className="button button-primary"
                  type="button"
                  onClick={startRecording}
                >
                  Start recording
                </button>
              ) : (
                <button
                  className="button button-stop"
                  type="button"
                  onClick={stopRecording}
                >
                  Stop recording
                </button>
              )}

              {audioUrl && !recording && (
                <div className="audio-preview">
                  <p>Recording ready</p>
                  <audio controls src={audioUrl}>
                    Your browser does not support audio playback.
                  </audio>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => {
                      URL.revokeObjectURL(audioUrl);
                      setAudioUrl("");
                    }}
                  >
                    Record again
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="field">
            <label htmlFor="photo">Supporting photo (optional)</label>
            <input
              id="photo"
              type="file"
              accept="image/*"
              onChange={(event) =>
                setPhoto(event.target.files?.[0] || null)
              }
            />
            {photo && <span className="field-help">Selected: {photo.name}</span>}
          </div>


          {message && !ticket && (
            <div className="message" role="status">
              {message}
            </div>
          )}

          {ticket && (
            <div className="message" role="status">
              <h3>Complaint registered!</h3>
              <p>Ticket number: {ticket.ticket_number}</p>
              <p>Status: {ticket.status}</p>
            </div>
          )}


          <button
            className="button button-submit"
            type="submit"
            disabled={loading}
          >
            {loading ? "Submitting..." : "Submit complaint →"}
          </button>

          <p className="privacy-note">
            Your complaint details should be handled securely.
          </p>
        </form>

        <footer className="footer">
          VoiceDesk · Multilingual support for everyone
        </footer>
      </main>
    </div>
  );
}

export default App;
