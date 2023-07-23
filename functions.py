import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
from datetime import timedelta
import pandas as pd
import json
import numpy as np
import time
import re
import urllib.request
from bs4 import BeautifulSoup as bs
import mimetypes
from socket import timeout
from transformers import pipeline

summarizer = None
max_letters_input = 1000
max_letters_output = 500

def changes_from_press(stock_data, press_date, period):
    next_day = timedelta(days=1)
    time_after_release = timedelta(days=period)
    
    next_trading_day = press_date
    
    # try to obtain share price at open on press release date
    day_1_price = stock_data[stock_data['Date'] == press_date]['Open'].values

    # increase the date till the first available open price is found
    num_days = 1
    while not day_1_price:
        if num_days > 30:
            return None
        
        next_trading_day = next_trading_day + next_day

        day_1_price = stock_data[stock_data['Date'] == (next_trading_day)]['Open'].values
        num_days = num_days + 1

    # get next day if available
    next_trading_day = next_trading_day + time_after_release
    day_2_price = stock_data[stock_data['Date'] == (next_trading_day)]['Close'].values

    # increase the date till the second available close price is found
    num_days = 1
    while not day_2_price:
        if num_days > 30: 
            return None
        
        next_trading_day = next_trading_day + next_day

        day_2_price = stock_data[stock_data['Date'] == (next_trading_day + next_day)]['Close'].values
        num_days = num_days + 1
    
    # calcualte percent difference between share prices
    pct_change = ((day_2_price - day_1_price) / day_2_price)*100
    pct_change = pct_change[0]
    return pct_change

def get_sector(ticker):
    # yfinance to get the sector for the give stock
    sector = yf.Ticker(ticker).info
    return sector['sector']

def get_df(ticker):
    # use yfinance to extract the share price over time
    stock_data = yf.Ticker(ticker)

    # get historical market data
    stock_hist = stock_data.history(period="max")

    # make index a column and convert to datetime
    stock_hist.reset_index(inplace=True)
    stock_hist['Date'] = pd.to_datetime(stock_hist['Date']).dt.date
    
    # daily percentage change at close
    stock_hist['Pct_Close'] = stock_hist['Close'].pct_change()*100
    return stock_hist

def normalise_to_index(pct_change, date, index_hist):
    # get index price on same day as pres
    index_price = changes_from_press(index_hist, date, 1)  
    # in case error or no change just return none
    if index_price == None:
        return (None, None)  
    # normlise price by minusing the index change on the same day
    norm_price = pct_change - index_price
    return index_price, norm_price

# convert press statements to json data and save to file
def save_data(data, ticker, sector):
    # column names
    columns = ['date', 'press_title', 'release_url',
               'content', '1d_change', 'index_price','norm_price']

    # convert to dataframe and drop and columns with na 
    data = pd.DataFrame(data, columns=columns)
    data = data.dropna()
    
    # convert the table to json where column names are keys and rows are the values 
    result = data.to_json(orient="table", index=True)
    press_data = json.loads(result)['data'] # convert back the dict
    
    # embed json into dictionary
    dict_data = {
            'ticker': ticker,
            'sector': sector,
            'data': press_data,
        },

    # open file and write dict to json
    with open(f'./data/press_data/{ticker}.json', 'w', encoding='utf-8') as f:
        json.dump(dict_data, f, ensure_ascii=False, indent=4)

    return

def summerise_content(content):
    # if the press statement is to long use bart to shorten by summeriseing
    summarizer = pipeline("summarization", model="facebook/bart-large-cnn")

    content = summarizer(content, max_length=max_letters_output, min_length=200, do_sample=False)[0]['summary_text']
    return content

# Extract press data from the html header function
def get_content_header(url, tag, attrs):

    # parameters to make sure html is extract succesfully
    user_agent = 'Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.9.0.7) Gecko/2009021910 Firefox/3.0.7'
    headers = {'User-Agent':user_agent,} 

    request = urllib.request.Request(url, None, headers) # the assembled request
    
    # if something goes wrong with request html returns error
    try:
        response = urllib.request.urlopen(request, timeout=8)
    except urllib.request.HTTPError as inst:
        return 'error_http'
    except timeout:
        return 'error_timeout'

    # not implemented pdf parseing so has to error
    content_type = response.headers['content-type']
    extension = mimetypes.guess_extension(content_type)
    if extension == '.pdf':
        return 'error_pdf'
        
    # head html in soup data type 
    soup = bs(response, 'html.parser')
    soup_head = soup.head
    
    if soup_head.find(tag, attrs=attrs) != None:
        content = soup_head.find(tag, attrs=attrs)['content']
    else:
        content = None

    # get text if element is found
    if soup_body.find(tag, attrs=attrs) != None and content == None:
        content = soup_head.find(tag, attrs=attrs)['content'].text

    # extract title if description is missing
    if attrs == {'property':'og:description'} and content == None:
        content = soup_head.find(tag, attrs={'property':'og:title'})['content']

    # extract title if description is missing
    if attrs == {'name':'twitter:description'} and content == None:
        content = soup_head.find(tag, attrs={'name':'twitter:title'})['content']

    # when statement is to long summrise
    if len(content) > max_letters_input:
        content = summerise_content(content)
    return content

def get_content_first_ele(url, tag, attrs):

    user_agent = 'Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.9.0.7) Gecko/2009021910 Firefox/3.0.7'

    headers = {'User-Agent':user_agent,} 

    request = urllib.request.Request(url, None, headers) #The assembled request
    response = urllib.request.urlopen(request)

    content_type = response.headers['content-type']
    extension = mimetypes.guess_extension(content_type)
    if extension == '.pdf':
        return 'error_pdf'
        
    soup = bs(response, 'html.parser')
    content = soup.find(tag, attrs=attrs).text
    if len(content) > max_letters_input:
        content = summerise_content(content)
    return content


def hd_specfic(url):
    user_agent = 'Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.9.0.7) Gecko/2009021910 Firefox/3.0.7'

    headers = {'User-Agent':user_agent,} 

    request = urllib.request.Request(url, None, headers) #The assembled request
    response = urllib.request.urlopen(request)

    content_type = response.headers['content-type']
    extension = mimetypes.guess_extension(content_type)
    if extension == '.pdf':
        return 'error_pdf'

    soup = bs(response, 'html.parser')
    content = soup.find('div', {'class': 'xn-content'})
    if content == None:
        content = soup.find('div', {'class': 'pr-body'}) 
        content = content.find('p').text
        return content

    content = content.find('p').text

    return content

def isrg_specfic(url):
    user_agent = 'Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.9.0.7) Gecko/2009021910 Firefox/3.0.7'

    headers = {'User-Agent':user_agent,} 

    response = requests.get(url)

    content_type = response.headers['content-type']
    extension = mimetypes.guess_extension(content_type)
    if extension == '.pdf':
        return 'error_pdf'

    soup = bs(response.text, 'html.parser')

    content = soup.find('ul', {'type': 'disc'})
    if content == None:
        content = soup.find('p').text
    else:
        content = content.text

    if len(content) > max_letters_input:
        content = summerise_content(content)
    return content


def get_content_xom(url):
    user_agent = 'Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.9.0.7) Gecko/2009021910 Firefox/3.0.7'

    headers = {'User-Agent':user_agent,} 

    response = requests.get(url)

    content_type = response.headers['content-type']
    extension = mimetypes.guess_extension(content_type)
    if extension == '.pdf':
        return 'error_pdf'

    soup = bs(response.text, 'html.parser')

    content = soup.find('ul', {'type': 'bwlistdisc'})


    if content == None: 
        content = soup.find('p').text
    else:
        content = content.text

    if len(content) > max_letters_input:
        content = summerise_content(content)


    return content

