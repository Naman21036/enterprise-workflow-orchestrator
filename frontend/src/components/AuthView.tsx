import { FormEvent, useState } from 'react';
import { Eye, EyeOff, LockKeyhole, Workflow } from 'lucide-react';
import type { RegistrationPayload } from '../services/api';

type Props = {
  onSignIn: (username: string, password: string) => Promise<void>;
  onRegister: (payload: RegistrationPayload) => Promise<void>;
};

export function AuthView({ onSignIn, onRegister }: Props) {
  const [mode, setMode] = useState<'signin' | 'register'>(() => new URLSearchParams(window.location.search).get('register') === '1' ? 'register' : 'signin');
  const [fullName, setFullName] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  function switchMode(next: 'signin' | 'register') {
    setMode(next);
    setError('');
    setMessage('');
    window.history.replaceState(null, '', next === 'register' ? '/?register=1' : '/');
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError('');
    setMessage('');
    if (mode === 'register' && password !== confirmation) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'signin') {
        await onSignIn(username, password);
      } else {
        await onRegister({
          full_name: fullName,
          username,
          email,
          password,
          confirm_password: confirmation,
        });
        setMode('signin');
        setUsername(username.trim());
        setPassword('');
        setConfirmation('');
        setMessage('Account created. Sign in with your new credentials.');
        window.history.replaceState(null, '', '/');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : mode === 'signin' ? 'Sign in failed.' : 'Registration could not be completed.');
    } finally {
      setBusy(false);
    }
  }

  const registering = mode === 'register';
  return <main className="auth-screen">
    <section className={`auth-card ${registering ? 'auth-register-card' : ''}`} aria-labelledby="auth-title">
      <div className="auth-brand"><span className="brand-mark"><Workflow size={19}/></span><span><b>APEX</b><small>AUTOMATION</small></span></div>
      <div className="auth-icon"><LockKeyhole size={18}/></div>
      <h1 id="auth-title">{registering ? 'Create operator account' : 'Operator sign in'}</h1>
      <p>{registering ? 'Create an account to join the default automation workspace.' : 'Authenticate to access your tenant’s automation workspace.'}</p>
      <form onSubmit={submit}>
        {registering && <>
          <label htmlFor="operator-full-name">Full name</label>
          <input id="operator-full-name" autoComplete="name" value={fullName} onChange={event => setFullName(event.target.value)} required maxLength={160}/>
        </>}
        <label htmlFor="operator-username">{registering ? 'Username' : 'Username or email'}</label>
        <input id="operator-username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required maxLength={160}/>
        {registering && <>
          <label htmlFor="operator-email">Email address</label>
          <input id="operator-email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required maxLength={320}/>
        </>}
        <label htmlFor="operator-password">Password</label>
        <div className="auth-password-wrap">
          <input id="operator-password" type={showPassword ? 'text' : 'password'} autoComplete={registering ? 'new-password' : 'current-password'} value={password} onChange={event => setPassword(event.target.value)} required minLength={registering ? 12 : 1} maxLength={1024}/>
          <button className="auth-password-toggle" type="button" onClick={() => setShowPassword(value => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword}>{showPassword ? <EyeOff size={15}/> : <Eye size={15}/>}</button>
        </div>
        {registering && <>
          <label htmlFor="operator-confirm-password">Confirm password</label>
          <input id="operator-confirm-password" type={showPassword ? 'text' : 'password'} autoComplete="new-password" value={confirmation} onChange={event => setConfirmation(event.target.value)} required minLength={12} maxLength={1024}/>
          <small className="auth-help">Use at least 12 characters. New accounts receive the standard operator role in the default tenant.</small>
        </>}
        {message && <div className="auth-success" role="status">{message}</div>}
        {error && <div className="auth-error" role="alert">{error}</div>}
        <button className="button primary auth-submit" type="submit" disabled={busy}>{busy ? (registering ? 'Creating account…' : 'Signing in…') : registering ? 'Create account' : 'Sign in'}</button>
      </form>
      <button type="button" className="auth-mode-toggle" onClick={() => switchMode(registering ? 'signin' : 'register')} disabled={busy}>
        {registering ? 'Already have an account? Sign in' : 'Create an account'}
      </button>
      <small className="auth-footnote">Protected with tenant-scoped operator access</small>
    </section>
  </main>;
}
