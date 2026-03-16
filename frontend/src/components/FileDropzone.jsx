import { useRef, useState } from "react";

const ACCEPT_TYPES = "application/pdf,image/png,image/jpeg,image/webp";

const ACCEPT_SET = new Set([
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/webp"
]);

export default function FileDropzone({
  id,
  file = null,
  onChange,
  label,
  hint,
  accept = ACCEPT_TYPES
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);
  const dragCounter = useRef(0);

  const handleFile = (nextFile) => {
    if (!nextFile) return;
    if (!ACCEPT_SET.has(nextFile.type)) return;
    onChange(nextFile);
  };

  const handleDragEnter = (event) => {
    event.preventDefault();
    event.stopPropagation();
    dragCounter.current += 1;
    if (dragCounter.current === 1) {
      setDragging(true);
    }
  };

  const handleDragLeave = (event) => {
    event.preventDefault();
    event.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current === 0) {
      setDragging(false);
    }
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    event.stopPropagation();
  };

  const handleDrop = (event) => {
    event.preventDefault();
    event.stopPropagation();
    dragCounter.current = 0;
    setDragging(false);

    const droppedFile = event.dataTransfer?.files?.[0];
    if (droppedFile) {
      handleFile(droppedFile);
    }
  };

  const handleInputChange = (event) => {
    const nextFile = event.target.files?.[0] || null;
    if (nextFile) {
      handleFile(nextFile);
    }
  };

  const handleClick = () => {
    inputRef.current?.click();
  };

  const handleKeyDown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handleClick();
    }
  };

  return (
    <div
      className={`attachment-dropzone${dragging ? " attachment-dropzone-active" : ""}`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      role="button"
      tabIndex={0}
    >
      <span className="attachment-dropzone-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 16V4" />
          <path d="m7 9 5-5 5 5" />
          <path d="M4 20h16" />
        </svg>
      </span>
      <strong>{file ? file.name : label}</strong>
      <span>{hint}</span>
      <input
        ref={inputRef}
        id={id}
        type="file"
        accept={accept}
        onChange={handleInputChange}
      />
    </div>
  );
}
