from config import Config as config
from job_states import JobStates
from utils import *

import redis
from rq import get_current_job
import time
import json

import sys
import random

from sklearn.metrics import mean_squared_error, r2_score
import numpy as np

import requests

POOL = redis.ConnectionPool(host=config.redis_host, port=config.redis_port, db=0)

def _evaluate(predicted_heights, true_heights, context):
    """
        takes a list of predicted heights and true heights and computes the score
    """
    score = 0
    secondary_score = 0

    """
    for k in range(100):
        time.sleep(random.randint(0,100)/1000.0)
        percent_complete = k*1.0/100 * 100
        update_progress(context, percent_complete, "")
        # print "Context Response Channel ::: ", context['response_channel']
        # if k%20==0:
        #     # print "Update : ", percent_complete
        score += random.randint(1,100)*1.0/0.7 / 100
        secondary_score += random.randint(1,100)*1.0/0.7 / 100
    """
   
    predicted_heights = np.array(predicted_heights)
    true_heights = np.array(true_heights)
    if predicted_heights.shape != true_heights.shape:
        raise Exception("Wrong number of predictions provided. Expected a variable of shape %s, but received a variable of shape %s instead" % ( str(true_heights.shape), str(predicted_heights.shape) ))

    score = r2_score(true_heights, predicted_heights)
    secondary_score = mean_squared_error(true_heights, predicted_heights)

    _result_object = {
        "score" : score,
        "score_secondary" : secondary_score,
    }
    return _result_object

def _submit(predicted_heights, true_heights, context):
    """
        takes a list of predicted heights and actual heights and computes the score

        and prepares the plots for submission to the leaderboard
    """
    #_result_object = _evaluate(predicted_heights[:50], true_heights[:50], context)
    _result_object = _evaluate(predicted_heights, true_heights, context)
    #_result_object["comment"] = ""
    #_result_object["media_large"] = "https://upload.wikimedia.org/wikipedia/commons/4/44/Drift_Diffusion_Model_Accumulation_to_Threshold_Example_Graphs.png"
    #_result_object["media_thumbnail"] = "https://upload.wikimedia.org/wikipedia/commons/4/44/Drift_Diffusion_Model_Accumulation_to_Threshold_Example_Graphs.png"
    #_result_object["media_content_type"] = "image/jpeg"


    # Upload result to crowdai
    headers = {'Authorization' : 'Token token='+config.CROWDAI_TOKEN, "Content-Type":"application/vnd.api+json"}

    _payload = _result_object
    _payload['challenge_client_name'] = config.challenge_id
    _payload['api_key'] = context['api_key']
    _payload['grading_status'] = 'graded'
    _payload['comment'] = "" #TODO: Allow participants to add comments to submissions

    print "Making POST request...."
    r = requests.post(config.CROWDAI_GRADER_URL, params=_payload, headers=headers, verify=False)
    print "Status Code : ",r.status_code
    
    if r.status_code == 202:
        data = json.loads(r.text)
        submission_id = str(data['submission_id'])
        _payload['data'] = predicted_heights
        context['redis_conn'].set(config.challenge_id+"::submissions::"+submission_id, json.dumps(_payload)) 
        pass #TODO: Add success message
    else:
        raise Exception(str(r.text))
    return _result_object

def _update_job_event(_context, data):
    """
        Helper function to serialize JSON
        and make sure redis doesnt messup JSON validation
    """
    redis_conn = _context['redis_conn']
    response_channel = _context['response_channel']
    data['data_sequence_no'] = _context['data_sequence_no']

    redis_conn.rpush(response_channel, json.dumps(data))

def job_execution_wrapper(data):
    redis_conn = redis.Redis(connection_pool=POOL)
    job = get_current_job()

    _context = {}
    _context['redis_conn'] = redis_conn
    _context['response_channel'] = data['broker_response_channel']
    _context['job_id'] = job.id
    _context['data_sequence_no'] = data['data_sequence_no']
    _context['api_key'] = data['extra_params']['api_key']

    # Register Job Running event
    _update_job_event(_context, job_running_template(_context['data_sequence_no'], job.id))
    result = {}
    try:
        if data["function_name"] == "submit":
            # Run the job
            true_heights = np.load("test_heights.npy")
            result = _submit(data["data"], true_heights, _context)
            # Register Job Complete event
            _update_job_event(_context, job_info_template(_context, "Scores Submitted Successfully ! Coefficient of Determination(R^2) : %s ; MSE : %s" % (str(result['score']), str(result['score_secondary'])) ))
            _update_job_event(_context, job_complete_template(_context, result))
        else:
            _error_object = job_error_template(job.id, "Function not implemented error")
            _update_job_event(_context, job_error_template(job.id, result))
            result = _error_object
    except Exception as e:
        _error_object = job_error_template(_context['data_sequence_no'], job.id, str(e))
        _update_job_event(_context, _error_object)
    return result
