/*
Top-level frontend entry: renders the React App component into the document root.
This file is the Vite/React entry point; realtime UI calls the backend via `frontend/src/api/client.ts`.
*/

import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
