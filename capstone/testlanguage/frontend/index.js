// 샘플 JavaScript 파일 - import / require / 상대경로 테스트용
import React from 'react';
import { useState } from 'react';
import axios from 'axios';
const _ = require('lodash');
import express from 'express';

// 상대경로 import - 외부 라이브러리가 아니므로 제외되어야 함
import './App.css';
import Header from './components/Header';
const utils = require('../utils/helpers');

function App() {
  const [data, setData] = useState(null);
  return React.createElement('div', null, 'hello');
}

export default App;
