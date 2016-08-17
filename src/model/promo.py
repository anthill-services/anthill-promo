
from tornado.gen import coroutine, Return
from common.database import DatabaseError
from common.model import Model

import ujson
import re
import random


class PromoError(Exception):
    pass


class PromoNotFound(Exception):
    pass


class PromoModel(Model):
    PROMO_PATTERN = re.compile("[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}")

    def __init__(self, db):
        self.db = db

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["promo_code", "promo_code_users"]

    def random_code(self, n):
        return ''.join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(n))

    def random(self):
        return self.random_code(4) + "-" + self.random_code(4) + "-" + self.random_code(4)

    def validate(self, code):
        if not re.match(PromoModel.PROMO_PATTERN, code):
            raise PromoError("Promo code is not valid (should be XXXX-XXXX-XXXX)")

    @coroutine
    def new_promo(self, gamespace_id, promo_key, promo_use_amount, promo_expires, promo_contents):

        if not isinstance(promo_contents, dict):
            raise PromoError("Contents is not a dict")

        try:
            yield self.find_promo(gamespace_id, promo_key)
        except PromoNotFound:
            pass
        else:
            raise PromoError("Promo code '{0}' already exists.".format(promo_key))

        try:
            result = yield self.db.insert("""
                INSERT INTO `promo_code`
                (`gamespace_id`, `code_key`, `code_amount`, `code_expires`, `code_contents`)
                VALUES (%s, %s, %s, %s, %s);
            """, gamespace_id, promo_key, promo_use_amount, promo_expires, ujson.dumps(promo_contents))
        except DatabaseError as e:
            raise PromoError("Failed to add new promo code: " + e.args[1])

        raise Return(result)

    @coroutine
    def find_promo(self, gamespace_id, promo_key):
        try:
            result = yield self.db.get("""
                SELECT *
                FROM `promo_code`
                WHERE `code_key`=%s AND `gamespace_id`=%s;
            """, promo_key, gamespace_id)
        except DatabaseError as e:
            raise PromoError("Failed to find promo code: " + e.args[1])

        if result is None:
            raise PromoNotFound()

        raise Return(result)

    @coroutine
    def get_promo(self, gamespace_id, promo_id):
        try:
            result = yield self.db.get("""
                SELECT *
                FROM `promo_code`
                WHERE `code_id`=%s AND `gamespace_id`=%s;
            """, promo_id, gamespace_id)
        except DatabaseError as e:
            raise PromoError("Failed to get promo code: " + e.args[1])

        if result is None:
            raise PromoNotFound()

        raise Return(result)

    @coroutine
    def delete_promo(self, gamespace_id, promo_id):
        try:
            yield self.db.execute("""
                DELETE
                FROM `promo_code`
                WHERE `code_id`=%s AND `gamespace_id`=%s;
            """, promo_id, gamespace_id)

            yield self.db.execute("""
                DELETE
                FROM `promo_code_users`
                WHERE `code_id`=%s AND `gamespace_id`=%s;
            """, promo_id, gamespace_id)
        except DatabaseError as e:
            raise PromoError("Failed to delete content: " + e.args[1])

    @coroutine
    def update_promo(self, gamespace_id, promo_id, promo_key, promo_use_amount, promo_expires, promo_contents):

        if not isinstance(promo_contents, dict):
            raise PromoError("Contents is not a dict")

        try:
            yield self.db.execute("""
                UPDATE `promo_code`
                SET `code_key`=%s, `code_amount`=%s, `code_expires`=%s, `code_contents`=%s
                WHERE `code_id`=%s AND `gamespace_id`=%s;
            """, promo_key, promo_use_amount, promo_expires, ujson.dumps(promo_contents), promo_id, gamespace_id)
        except DatabaseError as e:
            raise PromoError("Failed to update content: " + e.args[1])

    @coroutine
    def get_promo_usages(self, gamespace_id, promo_id):
        usages = yield self.db.query("""
            SELECT `account_id`
            FROM `promo_code_users`
            WHERE `code_id`=%s AND `gamespace_id`=%s;
        """, promo_id, gamespace_id)

        raise Return([str(usage["account_id"]) for usage in usages])

    @coroutine
    def use_promo(self, gamespace_id, account_id, promo_key):
        with (yield self.db.acquire(auto_commit=False)) as db:
            promo = yield db.get("""
                SELECT `code_id`, `code_contents`, `code_amount`
                FROM `promo_code`
                WHERE `code_key`=%s AND `gamespace_id`=%s AND `code_amount` > 0 AND `code_expires` > NOW()
                FOR UPDATE;
            """, promo_key, gamespace_id)

            if not promo:
                yield db.commit()
                raise PromoNotFound()

            promo_id = promo["code_id"]
            promo_contents = promo["code_contents"]
            promo_amount = promo["code_amount"]

            used = yield db.get("""
                SELECT *
                FROM `promo_code_users`
                WHERE `code_id`=%s AND `gamespace_id`=%s AND `account_id`=%s;
            """, promo_id, gamespace_id, account_id)

            if used:
                yield db.commit()
                raise PromoError("Code already used by this user")

            yield db.insert("""
                INSERT INTO `promo_code_users`
                (`gamespace_id`, `code_id`, `account_id`)
                VALUES (%s, %s, %s);
            """, gamespace_id, promo_id, account_id)

            promo_amount -= 1

            yield db.execute("""
                UPDATE `promo_code`
                SET `code_amount` = %s
                WHERE `code_id`=%s AND `gamespace_id`=%s;
            """, promo_amount, promo_id, gamespace_id)
            yield db.commit()

            ids = promo_contents.keys()
            contents_result = []

            contents = yield db.query("""
                SELECT `content_json`, `content_id`
                FROM `promo_contents`
                WHERE `content_id` IN (%s)
            """, ",".join(ids))

            for cnt in contents:
                result = {
                    "payload": cnt["content_json"],
                    "amount": promo_contents[str(cnt["content_id"])]
                }
                contents_result.append(result)

            raise Return({
                "result": contents_result
            })