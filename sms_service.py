import httpx
import json

class HeroSMS:

    def __init__(self, api_key):
        self.__api_url = "https://hero-sms.com/stubs/handler_api.php"
        self.api_key = api_key
        self.debug_mode = False
        self.client = httpx.AsyncClient(timeout=10.0)

        self.__CODES = {
            'STATUS_WAIT_CODE': 'Waiting for sms',
            'STATUS_WAIT_RETRY': 'Past Inappropriate Code - Waiting for Code Refinement',
            'STATUS_WAIT_RESEND ': 'Waiting for re-sending SMS',
            'STATUS_CANCEL': 'Activation canceled',
            'STATUS_OK': 'Code received',
            'FULL_SMS': 'Full text received'
        }

        self.__RENT_CODES = {
            'STATUS_WAIT_CODE': 'Waiting for the first SMS',
            'STATUS_FINISH': 'Rent paid and completed',
            'STATUS_CANCEL': 'Rent canceled with a refund',
        }

        self.__ERRORS = {
            'NO_NUMBERS': 'There are no free numbers for receiving SMS from the current service',
            'NO_BALANCE': 'Not enough funds',
            'BAD_ACTION': 'Invalid action (action parameter)',
            'BAD_SERVICE': 'Incorrect service name (service parameter)',
            'BAD_KEY': 'Invalid API access key',
            'ERROR_SQL': 'One of the parameters has an invalid value.',
            'SQL_ERROR': 'One of the parameters has an invalid value.',
            'NO_ACTIVATION': 'The specified activation id does not exist',
            'BAD_STATUS': 'Attempt to establish a non-existent status',
            'STATUS_CANCEL': 'Current activation canceled and no longer available',
            'BANNED': 'Account is blocked',
            'NO_CONNECTION': 'No connection to servers sms-activate',
            'ACCOUNT_INACTIVE': 'No numbers available',
            'NO_ID_RENT': 'Rent id not specified',
            'INVALID_PHONE': 'The number was not rented by you (wrong rental id)',
            'STATUS_FINISH': 'Rent paid and completed',
            'INCORECT_STATUS': 'Missing or incorrect status',
            'CANT_CANCEL': 'Unable to cancel the lease (more than 20 minutes have passed)',
            'ALREADY_FINISH': 'The lease has already been completed',
            'ALREADY_CANCEL': 'The lease has already been canceled',
            'WRONG_OPERATOR': 'Lease Transfer Operator is not MTT',
            'NO_YULA_MAIL': 'To buy a number from the mail group holding, you must have at least 500 rubles on your account',
            'WHATSAPP_NOT_AVAILABLE': 'No WhatsApp numbers available',
            'NOT_INCOMING': 'Activation is not call-verified activation',
            'INVALID_ACTIVATION_ID': 'Invalid activation id',
            'WRONG_ADDITIONAL_SERVICE': 'Invalid additional service (only services for forwarding are allowed)',
            'WRONG_ACTIVATION_ID': 'Invalid parental activation ID',
            'WRONG_SECURITY': 'An error occurred when trying to transfer an activation ID without forwarding, or a completed / inactive activation',
            'REPEAT_ADDITIONAL_SERVICE': 'The error occurs when you try to order the purchased service again',
            'NO_KEY': 'API key missing',
            'OPERATORS_NOT_FOUND': ' Operators not found'
        }

    def version(self):
        return "1.5"

    def check_error(self, response):
        if self.__ERRORS.get(response) == None:
            return False
        return True

    def get_error(self, error):
        return self.__ERRORS.get(error)

    def __debugLog(self, data):
        if self.debug_mode:
            print('[Debug]', data)

    async def response(self, action, response):
        self.__debugLog(response)
        if self.check_error(response):
            return {"error": response, "message": self.get_error(response)}
        elif not str(response):
            return {"error": response, "message": "Server error, try again"}

        if action == "getNumbersStatus":
            result = json.loads(response)
            return result

        elif action == "getBalance":
            response = str(response[15:])
            result = {"balance": response}
            return result

        elif action == "getBalanceAndCashBack":
            response = str(response[15:])
            result = {"balance": response}
            return result

        elif action == "getNumber":
            response = str(response[14:])
            data = response.split(":")
            activation_id = int(data[0])
            phone = int(data[1])
            result = {"activation_id": activation_id, "phone": phone}
            return result

        elif action == "getNumberV2":
            result = json.loads(response)
            return result

        elif action == "getMultiServiceNumber":
            result = json.loads(response)
            return result

        elif action == "getPrices":
            result = json.loads(response)
            return result

        elif action == "getCountries":
            result = json.loads(response)
            return result

        elif action == "getQiwiRequisites":
            result = json.loads(response)
            return result

        elif action == "getAdditionalService":
            response = str(response[11:])
            data = response.split(":")
            id = int(data[0])
            phone = int(data[1])
            result = {"id": id, "phone": phone}
            return result

        elif action == "getRentServicesAndCountries":
            result = json.loads(response)
            return result

        elif action == "getRentNumber":
            result = json.loads(response)
            return result

        elif action == "getRentStatus":
            result = json.loads(response)
            return result

        elif action == "setRentStatus":
            result = json.loads(response)
            return result
        elif action == "getRentList":
            result = json.loads(response)
            return result

        elif action == "continueRentNumber":
            result = json.loads(response)
            return result

        elif action == "getContinueRentPriceNumber":
            result = json.loads(response)
            return result

        elif action == "getTopCountriesByService":
            result = json.loads(response)
            return result

        elif action == "getIncomingCallStatus":
            result = json.loads(response)
            return result

        elif action == "getOperators":
            result = json.loads(response)
            return result

        elif action == "getActiveActivations":
            result = json.loads(response)
            return result

        elif action == "createTaskForCall":
            result = json.loads(response)
            if 'msg' in result:
                result['message'] = result.pop('msg')
            return result
        elif action == "getOutgoingCalls":
            result = json.loads(response)
            return result
        elif action == "cancelActivation":
            if type(response) is str:
                response = json.loads(response)
                return response
            else:
                return True
        else:
            return response

    def activationStatus(self, status):
        return {"status": status, "message": self.__CODES.get(status)}

    def rentStatus(self, status):
        return self.__RENT_CODES.get(status)

    async def getBalance(self):
        payload = {'api_key': self.api_key, 'action': 'getBalance'}
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getBalance", r.text)

    async def getBalanceAndCashBack(self):
        payload = {'api_key': self.api_key, 'action': 'getBalanceAndCashBack'}
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getBalanceAndCashBack", r.text)

    async def getNumbersStatus(self, country=None, operator=None):
        payload = {'api_key': self.api_key, 'action': 'getNumbersStatus'}
        if country is not None:
            payload['country'] = country
        if operator:
            payload['operator'] = operator
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getNumbersStatus", r.text)

    async def getNumber(self, service=None, forward=None, freePrice=None, maxPrice=None, phoneException=None, operator=None,
                  ref=None, country=None, verification=None):
        payload = {'api_key': self.api_key, 'action': 'getNumber'}
        if service:
            payload['service'] = service
        if forward:
            payload['forward'] = forward
        if freePrice:
            payload['freePrice'] = freePrice
        if maxPrice:
            payload['maxPrice'] = maxPrice
        if phoneException:
            payload['phoneException'] = phoneException
        if operator:
            payload['operator'] = operator
        if ref:
            payload['ref'] = ref
        if country is not None:
            payload['country'] = country
        if verification:
            payload['verification'] = verification

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getNumber", r.text)

    async def getNumberV2(self, service=None, forward=None, freePrice=None, maxPrice=None, phoneException=None, operator=None,
                  ref=None, country=None, verification=None):
        payload = {'api_key': self.api_key, 'action': 'getNumberV2'}
        if service:
            payload['service'] = service
        if forward:
            payload['forward'] = forward
        if freePrice:
            payload['freePrice'] = freePrice
        if maxPrice:
            payload['maxPrice'] = maxPrice
        if phoneException:
            payload['phoneException'] = phoneException
        if operator:
            payload['operator'] = operator
        if ref:
            payload['ref'] = ref
        if country is not None:
            payload['country'] = country
        if verification:
            payload['verification'] = verification

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getNumberV2", r.text)

    async def getMultiServiceNumber(self, service=None, forward=None, operator=None, ref=None, country=None):
        payload = {'api_key': self.api_key, 'action': 'getMultiServiceNumber'}
        if service:
            payload['multiService'] = service
        if forward:
            payload['forward'] = forward
        if operator:
            payload['operator'] = operator
        if ref:
            payload['ref'] = ref
        if country is not None:
            payload['country'] = country
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getMultiServiceNumber", r.text)

    async def setStatus(self, id=None, forward=None, status=None, ):
        payload = {'api_key': self.api_key, 'action': 'setStatus'}
        if id:
            payload['id'] = id
        if forward:
            payload['forward'] = forward
        if status:
            payload['status'] = status
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("setStatus", r.text)

    async def getStatus(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getStatus'}
        if id:
            payload['id'] = id
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getStatus", r.text)
    
    async def getStatusV2(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getStatusV2'}
        if id:
            payload['id'] = id
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getStatusV2", r.text)

    async def getFullSms(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getFullSms'}
        if id:
            payload['id'] = id
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getFullSms", r.text)

    async def getPrices(self, service=None, country=None):
        payload = {'api_key': self.api_key, 'action': 'getPrices'}
        if service:
            payload['service'] = service
        if country is not None:
            payload['country'] = country
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getPrices", r.text)

    async def getCountries(self):
        payload = {'api_key': self.api_key, 'action': 'getCountries'}
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getCountries", r.text)

    async def getQiwiRequisites(self):
        payload = {'api_key': self.api_key, 'action': 'getQiwiRequisites'}
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getQiwiRequisites", r.text)

    async def getAdditionalService(self, id=None, service=None):
        payload = {'api_key': self.api_key, 'action': 'getAdditionalService'}
        if id:
            payload['id'] = id
        if service:
            payload['service'] = service
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getAdditionalService", r.text)

    async def getRentServicesAndCountries(self, time=None, operator=None, country=None):
        payload = {'api_key': self.api_key, 'action': 'getRentServicesAndCountries'}
        if time:
            payload['time'] = time
        if operator:
            payload['operator'] = operator
        if country is not None:
            payload['country'] = country

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getRentServicesAndCountries", r.text)

    async def getRentNumber(self, service=None, time=None, operator=None, country=None, url=None):
        payload = {'api_key': self.api_key, 'action': 'getRentNumber'}
        if service:
            payload['service'] = service
        if time:
            payload['time'] = time
        if operator:
            payload['operator'] = operator
        if country is not None:
            payload['country'] = country
        if url:
            payload['url'] = url

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getRentNumber", r.text)

    async def getRentStatus(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getRentStatus'}
        if id:
            payload['id'] = id

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getRentStatus", r.text)

    async def setRentStatus(self, id=None, status=None):
        payload = {'api_key': self.api_key, 'action': 'setRentStatus'}
        if id:
            payload['id'] = id
        if status:
            payload['status'] = status

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("setRentStatus", r.text)

    async def getRentList(self):
        payload = {'api_key': self.api_key, 'action': 'getRentList'}
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getRentList", r.text)

    async def continueRentNumber(self, id=None, time=None):
        payload = {'api_key': self.api_key, 'action': 'continueRentNumber'}
        if id:
            payload['id'] = id
        if time:
            payload['rent_time'] = time

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("continueRentNumber", r.text)

    async def getContinueRentPriceNumber(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getContinueRentPriceNumber'}
        if id:
            payload['id'] = id

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getContinueRentPriceNumber", r.text)

    async def getTopCountriesByService(self, service=None, freePrice=None):
        payload = {'api_key': self.api_key, 'action': 'getTopCountriesByService'}
        if service:
            payload['service'] = service
        if freePrice:
            payload['freePrice'] = freePrice

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getTopCountriesByService", r.text)

    async def getIncomingCallStatus(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getIncomingCallStatus'}
        if id:
            payload['activationId'] = id

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getIncomingCallStatus", r.text)

    async def getOperators(self, country=None):
        payload = {'api_key': self.api_key, 'action': 'getOperators'}
        if country is not None:
            payload['country'] = country

        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getOperators", r.text)

    async def getActiveActivations(self):
        payload = {'api_key': self.api_key, 'action': 'getActiveActivations'}
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getActiveActivations", r.text)

    async def createTaskForCall(self, activationId):
        payload = {'api_key': self.api_key, 'action': 'createTaskForCall'}
        payload['activationId'] = activationId
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("createTaskForCall", r.text)

    async def getOutgoingCalls(self, activationId=None, date=None):
        payload = {'api_key': self.api_key, 'action': 'getOutgoingCalls'}
        if activationId is not None:
            payload['activationId'] = activationId
        if date is not None:
            payload['date'] = date
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("getOutgoingCalls", r.text)
    
    async def cancelActivation(self, id):
        """Cancela una activación existente por su ID"""
        payload = {'api_key': self.api_key, 'action': 'cancelActivation'}
        payload['id'] = id
        r = await self.client.get(self.__api_url, params=payload)
        return await self.response("cancelActivation", r.text)

    async def close(self):
        """Cierra el cliente HTTP asíncrono"""
        await self.client.aclose()