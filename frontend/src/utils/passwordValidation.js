export function validatePasswordStrength(password, username = "") {
  const errors = [];
  if (password.length < 10) errors.push("Minimo 10 caracteres.");
  if (!/[A-Z]/.test(password)) errors.push("Incluye una mayuscula.");
  if (!/[a-z]/.test(password)) errors.push("Incluye una minuscula.");
  if (!/\d/.test(password)) errors.push("Incluye un numero.");
  if (!/[!@#$%^&*()_+\-=[\]{}|;:,.<>?]/.test(password)) {
    errors.push("Incluye un caracter especial.");
  }
  if (username && password.trim().toLowerCase() === username.trim().toLowerCase()) {
    errors.push("No puede ser igual al usuario.");
  }
  return errors;
}
