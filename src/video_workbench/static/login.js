const form = document.getElementById("login_form");
const password = document.getElementById("password");
const error = document.getElementById("error");
const submit = document.getElementById("submit");

form.addEventListener("submit", async event => {
  event.preventDefault();
  error.textContent = "";
  submit.disabled = true;
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({password: password.value})
    });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(value.error || "登录失败，请重试");
    window.location.replace("/");
  } catch (exception) {
    error.textContent = exception.message || "无法连接服务器";
    password.select();
  } finally {
    submit.disabled = false;
  }
});
