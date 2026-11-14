import {JobStatus,AttemptStatus} from '../../constants/enums.js';
import {esc} from '../../utils/dom.js';
export const status=v=>'<span class="status '+esc(v)+'">'+esc(JobStatus[v]||v)+'</span>';
export const attemptStatus=v=>'<span class="status '+esc(v)+'">'+esc(AttemptStatus[v]||v)+'</span>';
