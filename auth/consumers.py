from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncJsonWebsocketConsumer, JsonWebsocketConsumer


class DeliveryConsumer(JsonWebsocketConsumer):
    def connect(self):
        return super().connect()


class TestConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await super().connect()
        await self.send_json("Connected", False)

    async def send_json(self, content, close=False):

        self.send(content)

        return await super().send_json(content, close)

    async def receive_json(self, content, **kwargs):
        print(content)
        return await super().receive_json(content, **kwargs)

    async def listen_group(self, event):
        await self.send_json(event["message"])
