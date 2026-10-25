
import { useCallback, useEffect, useRef, useState } from "react";
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
  const [lookupTicketNumber, setLookupTicketNumber] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupError, setLookupError] = useState("");
  const [lookupResult, setLookupResult] = useState(null);
  const [queueStatus, setQueueStatus] = useState("");
  const [queueTickets, setQueueTickets] = useState([]);
  const [queueLoading, setQueueLoading] = useState(true);
  const [queueUpdatingTicket, setQueueUpdatingTicket] = useState("");
  const [queueError, setQueueError] = useState("");
  const [queueMessage, setQueueMessage] = useState("");
  const [queueStatusDrafts, setQueueStatusDrafts] = useState({});
  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);

  const loadAgentQueue = useCallback(async () => {
    setQueueLoading(true);
    setQueueError("");
    setQueueMessage("");

    try {
      const query = queueStatus
        ? `?status=${encodeURIComponent(queueStatus)}`
        : "";
      const response = await fetch(
        `http://127.0.0.1:8000/api/complaints/${query}`
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.error || "Unable to load the ticket queue. Please try again."
        );
      }

      setQueueTickets(data.complaints || []);
    } catch (error) {
      setQueueError(
        error.message || "Unable to connect to the server. Please try again."
      );
    } finally {
      setQueueLoading(false);
    }
  }, [queueStatus]);

  useEffect(() => {
    let isCurrent = true;

    async function loadQueueForFilter() {
      try {
        const query = queueStatus
          ? `?status=${encodeURIComponent(queueStatus)}`
          : "";
        const response = await fetch(
          `http://127.0.0.1:8000/api/complaints/${query}`
        );
        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(
            data.error || "Unable to load the ticket queue. Please try again."
          );
        }

        if (isCurrent) {
          setQueueTickets(data.complaints || []);
          setQueueError("");
        }
      } catch (error) {
        if (isCurrent) {
          setQueueError(
            error.message ||
              "Unable to connect to the server. Please try again."
          );
        }
      } finally {
        if (isCurrent) {
          setQueueLoading(false);
        }
      }
    }

    loadQueueForFilter();
    return () => {
      isCurrent = false;
    };
  }, [queueStatus]);

  async function updateQueueTicketStatus(complaint) {
    const status =
      queueStatusDrafts[complaint.ticket_number] || complaint.status;
    if (status === complaint.status || queueUpdatingTicket) {
      return;
    }

    setQueueUpdatingTicket(complaint.ticket_number);
    setQueueError("");
    setQueueMessage("");

    try {
      const response = await fetch(
        `http://127.0.0.1:8000/api/complaints/${encodeURIComponent(
          complaint.ticket_number
        )}/status/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: new URLSearchParams({ status }),
        }
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.error || "Unable to update this ticket. Please try again."
        );
      }

      setQueueTickets((currentTickets) =>
        currentTickets.map((ticketItem) =>
          ticketItem.ticket_number === complaint.ticket_number
            ? {
                ...ticketItem,
                status: data.status,
                updated_at: data.updated_at,
              }
            : ticketItem
        )
      );
      setQueueStatusDrafts((drafts) => ({
        ...drafts,
        [complaint.ticket_number]: data.status,
      }));
      setQueueMessage("Ticket status updated.");
    } catch (error) {
      setQueueError(
        error.message || "Unable to connect to the server. Please try again."
      );
    } finally {
      setQueueUpdatingTicket("");
    }
  }

  async function handleTicketLookup(event) {
    event.preventDefault();
    if (lookupLoading) {
      return;
    }

    setLookupLoading(true);
    setLookupError("");
    setLookupResult(null);

    try {
      const response = await fetch(
        `http://127.0.0.1:8000/api/complaints/${encodeURIComponent(
          lookupTicketNumber.trim()
        )}/`
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error("We couldn't find a ticket with that number.");
        }
        throw new Error(
          data.error || "Unable to retrieve the ticket. Please try again."
        );
      }

      setLookupResult(data);
    } catch (error) {
      setLookupError(
        error.message || "Unable to connect to the server. Please try again."
      );
    } finally {
      setLookupLoading(false);
    }
  }

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
    } catch {
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
      setMessage(
        data.message || "Your complaint has been submitted successfully!"
      );

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

        <section className="complaint-card ticket-lookup">
          <div className="card-heading">
            <div>
              <h2>Find an existing ticket</h2>
              <p>Enter your ticket UUID to check its status and transcript.</p>
            </div>
          </div>

          <form onSubmit={handleTicketLookup}>
            <div className="field">
              <label htmlFor="ticket-uuid">Ticket UUID</label>
              <input
                id="ticket-uuid"
                type="text"
                value={lookupTicketNumber}
                onChange={(event) =>
                  setLookupTicketNumber(event.target.value)
                }
                placeholder="Enter your ticket UUID"
                required
              />
            </div>
            <button
              className="button button-primary"
              type="submit"
              disabled={lookupLoading}
            >
              {lookupLoading ? "Finding ticket..." : "Find Ticket"}
            </button>
          </form>

          {lookupError && (
            <div className="message lookup-error" role="alert">
              {lookupError}
            </div>
          )}

          {lookupResult && (
            <div className="message lookup-result" role="status">
              <h3>Ticket details</h3>
              <p><strong>Ticket number:</strong> {lookupResult.ticket_number}</p>
              <p><strong>Status:</strong> {lookupResult.status}</p>
              <p>
                <strong>Voice transcription:</strong>{" "}
                {typeof lookupResult.original_transcript === "string" &&
                lookupResult.original_transcript.trim()
                  ? lookupResult.original_transcript
                  : "No transcript available."}
              </p>
              <p>
                <strong>Created:</strong>{" "}
                {new Date(lookupResult.created_at).toLocaleString()}
              </p>
              <p>
                <strong>Updated:</strong>{" "}
                {new Date(lookupResult.updated_at).toLocaleString()}
              </p>
              {lookupResult.possible_duplicate &&
                lookupResult.possible_duplicate_ticket_number && (
                  <p className="possible-duplicate">
                    <strong>Possible duplicate:</strong>{" "}
                    {lookupResult.possible_duplicate_ticket_number}
                  </p>
                )}
            </div>
          )}
        </section>

        <section className="complaint-card agent-queue">
          <div className="card-heading">
            <div>
              <h2>Agent ticket queue (demo)</h2>
              <p>
                Demo workflow only; agent access is not authenticated.
              </p>
            </div>
            <button
              className="button button-primary"
              type="button"
              onClick={loadAgentQueue}
              disabled={queueLoading}
            >
              {queueLoading ? "Refreshing..." : "Refresh"}
            </button>
          </div>

          <div className="field queue-filter">
            <label htmlFor="queue-status-filter">Filter by status</label>
            <select
              id="queue-status-filter"
              value={queueStatus}
              onChange={(event) => {
                setQueueLoading(true);
                setQueueStatus(event.target.value);
              }}
            >
              <option value="">All statuses</option>
              <option value="submitted">Submitted</option>
              <option value="processing">Processing</option>
              <option value="open">Open</option>
              <option value="in_progress">In Progress</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>
          </div>

          {queueError && (
            <div className="message lookup-error" role="alert">
              {queueError}
            </div>
          )}
          {queueMessage && (
            <div className="message" role="status">
              {queueMessage}
            </div>
          )}

          {!queueLoading && !queueError && queueTickets.length === 0 && (
            <p className="field-help">No tickets match this filter.</p>
          )}

          <div className="queue-list">
            {queueTickets.map((queueTicket) => (
              <article
                className="queue-ticket"
                key={queueTicket.ticket_number}
              >
                <div className="queue-ticket-heading">
                  <strong>{queueTicket.ticket_number}</strong>
                  <span className="step-label">{queueTicket.status}</span>
                </div>
                {queueTicket.possible_duplicate &&
                  queueTicket.possible_duplicate_ticket_number && (
                    <p className="possible-duplicate">
                      <strong>Possible duplicate of:</strong>{" "}
                      {queueTicket.possible_duplicate_ticket_number}
                    </p>
                  )}
                <p>
                  <strong>Language:</strong> {queueTicket.language}
                  {" · "}
                  <strong>Processing:</strong>{" "}
                  {queueTicket.processing_status.replaceAll("_", " ")}
                </p>
                {queueTicket.classification_status === "suggested" && (
                  <p>
                    <strong>Automated suggestions (unreviewed)</strong>
                    {" · "}
                    <strong>Category:</strong>{" "}
                    {queueTicket.category || "Uncategorized"}
                    {" · "}
                    <strong>Priority:</strong> {queueTicket.priority}
                  </p>
                )}
                <p>
                  <strong>
                    {queueTicket.classification_status === "suggested"
                      ? "Suggested summary:"
                      : "Summary:"}
                  </strong>{" "}
                  {queueTicket.summary || "No summary available."}
                </p>
                <p>
                  <strong>Voice transcription:</strong>{" "}
                  {queueTicket.original_transcript || "No transcript available."}
                </p>
                {queueTicket.english_translation && (
                  <p>
                    <strong>English translation:</strong>{" "}
                    {queueTicket.english_translation}
                  </p>
                )}
                <p>
                  <strong>Created:</strong>{" "}
                  {new Date(queueTicket.created_at).toLocaleString()}
                  {" · "}
                  <strong>Updated:</strong>{" "}
                  {new Date(queueTicket.updated_at).toLocaleString()}
                </p>
                <div className="queue-status-control">
                  <label htmlFor={`queue-status-${queueTicket.ticket_number}`}>
                    Update status
                  </label>
                  <select
                    id={`queue-status-${queueTicket.ticket_number}`}
                    value={
                      queueStatusDrafts[queueTicket.ticket_number] ||
                      queueTicket.status
                    }
                    onChange={(event) =>
                      setQueueStatusDrafts((drafts) => ({
                        ...drafts,
                        [queueTicket.ticket_number]: event.target.value,
                      }))
                    }
                  >
                    <option value="submitted">Submitted</option>
                    <option value="processing">Processing</option>
                    <option value="open">Open</option>
                    <option value="in_progress">In Progress</option>
                    <option value="resolved">Resolved</option>
                    <option value="closed">Closed</option>
                  </select>
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={() => updateQueueTicketStatus(queueTicket)}
                    disabled={
                      queueLoading ||
                      Boolean(queueUpdatingTicket) ||
                      (queueStatusDrafts[queueTicket.ticket_number] ||
                        queueTicket.status) === queueTicket.status
                    }
                  >
                    {queueUpdatingTicket === queueTicket.ticket_number
                      ? "Saving..."
                      : "Update"}
                  </button>
                </div>
              </article>
            ))}
          </div>
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
              {ticket.possible_duplicate &&
                ticket.possible_duplicate_ticket_number && (
                  <p className="possible-duplicate">
                    <strong>Possible duplicate of:</strong>{" "}
                    {ticket.possible_duplicate_ticket_number}
                  </p>
                )}
              {ticket.processing_status && (
                <p>
                  Processing: {ticket.processing_status.replaceAll("_", " ")}
                </p>
              )}
              {typeof ticket.original_transcript === "string" &&
                ticket.original_transcript.trim() && (
                  <p>
                    <strong>Voice transcription:</strong>{" "}
                    {ticket.original_transcript}
                  </p>
                )}
              {ticket.classification_status === "suggested" && (
                <>
                  <p><strong>Automated suggestions (unreviewed)</strong></p>
                  <p>Category: {ticket.category}</p>
                  <p>Priority: {ticket.priority}</p>
                  <p>Summary: {ticket.summary}</p>
                </>
              )}
              {ticket.processing_status === "translation_unavailable" && (
                <p>English translation is unavailable until a provider is configured.</p>
              )}
              {ticket.english_translation && (
                <p>
                  <strong>English translation:</strong>{" "}
                  {ticket.english_translation}
                </p>
              )}
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
