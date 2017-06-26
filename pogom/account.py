#!/usr/bin/python
# -*- coding: utf-8 -*-

import logging
import time
import random

from pgoapi import PGoApi
from pgoapi.exceptions import AuthException

from .fakePogoApi import FakePogoApi
from .utils import in_radius, generate_device_info
from .proxy import get_new_proxy

log = logging.getLogger(__name__)


class TooManyLoginAttempts(Exception):
    pass


class LoginSequenceFail(Exception):
    pass


# Create the API object that'll be used to scan.
def setup_api(args, status, account):
    # Create the API instance this will use.
    if args.mock != '':
        api = FakePogoApi(args.mock)
    else:
        identifier = account['username'] + account['password']
        device_info = generate_device_info(identifier)
        api = PGoApi(device_info=device_info)

    # New account - new proxy.
    if args.proxy:
        # If proxy is not assigned yet or if proxy-rotation is defined
        # - query for new proxy.
        if ((not status['proxy_url']) or
                ((args.proxy_rotation is not None) and
                 (args.proxy_rotation != 'none'))):

            proxy_num, status['proxy_url'] = get_new_proxy(args)
            if args.proxy_display.upper() != 'FULL':
                status['proxy_display'] = proxy_num
            else:
                status['proxy_display'] = status['proxy_url']

    if status['proxy_url']:
        log.debug('Using proxy %s', status['proxy_url'])
        api.set_proxy({
            'http': status['proxy_url'],
            'https': status['proxy_url']})

    return api


# Use API to check the login status, and retry the login if possible.
def check_login(args, account, api, position, proxy_url):
    app_version = int(args.api_version.replace('.', '0'))
    # Logged in? Enough time left? Cool!
    if api._auth_provider and api._auth_provider._ticket_expire:
        remaining_time = api._auth_provider._ticket_expire / 1000 - time.time()
        if remaining_time > 60:
            log.debug(
                'Credentials remain valid for another %f seconds.',
                remaining_time)
            return

    # Try to login. Repeat a few times, but don't get stuck here.
    log.info(
        'Logging in account {}...'.format(account['username']))
    num_tries = 0
    # One initial try + login_retries.
    while num_tries < (args.login_retries + 1):
        try:
            if proxy_url:
                api.set_authentication(
                    provider=account['auth_service'],
                    username=account['username'],
                    password=account['password'],
                    proxy_config={'http': proxy_url, 'https': proxy_url})
            else:
                api.set_authentication(
                    provider=account['auth_service'],
                    username=account['username'],
                    password=account['password'])
            break
        except AuthException:
            num_tries += 1
            log.error(
                ('Failed to login to Pokemon Go with account %s. ' +
                 'Trying again in %g seconds.'),
                account['username'], args.login_delay)
            time.sleep(args.login_delay)

    if num_tries > args.login_retries:
        log.error(
            ('Failed to login to Pokemon Go with account %s in ' +
             '%d tries. Giving up.'),
            account['username'], num_tries)
        raise TooManyLoginAttempts('Exceeded login attempts.')

    time.sleep(random.uniform(2, 4))

    try:  # 1 - Make an empty request to mimick real app behavior.
        request = api.create_request()
        request.call()
        time.sleep(random.uniform(.43, .97))
    except Exception as e:
        log.exception('Login for account %s failed.' +
                      ' Exception in call request: %s', account['username'],
                      repr(e))
        raise LoginSequenceFail('Failed during login sequence.')

    try:  # 2 - Get Player request.
        request = api.create_request()
        request.get_player(
            player_locale={
                'country': 'US',
                'language': 'en',
                'timezone': 'America/Denver'})
        response = request.call()
        account['tutorial_state'] = get_tutorial_state(response)
        if response['responses']['GET_PLAYER'].get('warn', False):
            account['warn'] = True
        if response['responses']['GET_PLAYER'].get('banned', False):
            account['banned'] = True
        time.sleep(random.uniform(.53, 1.1))
    except Exception as e:
        log.exception('Login for account %s failed.' +
                      ' Exception in get_player: %s', account['username'],
                      repr(e))
        raise LoginSequenceFail('Failed during login sequence.')

    try:  # 3 - Download Remote Config Version request.
        old_config = account.get('remote_config', {})
        request = api.create_request()
        request.download_remote_config_version(platform=1,
                                               app_version=app_version)
        request.check_challenge()
        request.get_hatched_eggs()
        request.get_inventory()
        request.check_awarded_badges()
        request.download_settings()
        response = request.call()
        parse_get_inventory(account, response)
        parse_download_settings(account, response)
        time.sleep(random.uniform(.53, 1.1))
    except Exception as e:
        log.exception('Error while downloading remote config: %s.', repr(e))
        raise LoginSequenceFail('Failed during login sequence.')

    # 4 - Get Asset Digest request.
    config = account.get('remote_config', {})
    if config.get('asset_time', 0) > old_config.get('asset_time', 0):
        req_count = 0
        i = random.randint(0, 3)
        result = 2
        page_offset = 0
        page_timestamp = 0
        time.sleep(random.uniform(.7, 1.2))
        while result == 2:
            try:
                request = api.create_request()
                request.get_asset_digest(
                    platform=1,
                    app_version=app_version,
                    paginate=True,
                    page_offset=page_offset,
                    page_timestamp=page_timestamp)
                request.check_challenge()
                request.get_hatched_eggs()
                request.get_inventory(last_timestamp_ms=account[
                    'last_timestamp_ms'])
                request.check_awarded_badges()
                request.download_settings(hash=account[
                    'remote_config']['hash'])
                response = request.call()
                parse_get_inventory(account, response)

                get_asset_digest = response['responses']['GET_ASSET_DIGEST']
                req_count += 1
                if i > 2:
                    time.sleep(random.uniform(1.4, 1.6))
                    i = 0
                else:
                    i += 1
                    time.sleep(random.uniform(.3, .5))
                    result = get_asset_digest.get('result', 0)
                    page_offset = get_asset_digest.get('page_offset', 0)
                    page_timestamp = get_asset_digest.get('timestamp_ms', 0)

                    time.sleep(random.uniform(.53, 1.1))
                    log.debug('Completed %d requests to get asset digest.',
                              req_count)

            except Exception as e:
                log.exception('Error while downloading Asset Digest: %s.',
                              repr(e))
                raise LoginSequenceFail('Failed during login sequence.')

    # 5 - Download Item Templates request.
    if config.get('template_time', 0) > old_config.get('template_time', 0):
        req_count = 0
        i = random.randint(0, 3)
        result = 2
        page_offset = 0
        page_timestamp = 0
        while result == 2:
            try:
                request = api.create_request()
                request.download_item_templates(paginate=True,
                                                page_offset=page_offset,
                                                page_timestamp=page_timestamp)
                request.check_challenge()
                request.get_hatched_eggs()
                request.get_inventory(last_timestamp_ms=account[
                    'last_timestamp_ms'])
                request.check_awarded_badges()
                request.download_settings(hash=account[
                    'remote_config']['hash'])
                response = request.call()
                parse_get_inventory(account, response)

                download_item_templates = response['responses'][
                    'DOWNLOAD_ITEM_TEMPLATES']
                req_count += 1
                if i > 2:
                    time.sleep(random.uniform(1.4, 1.6))
                    i = 0
                else:
                    i += 1
                    time.sleep(random.uniform(.3, .5))

                    result = download_item_templates.get('result', 0)
                    page_offset = download_item_templates.get('page_offset', 0)
                    page_timestamp = download_item_templates.get(
                        'timestamp_ms', 0)
                    log.debug('Completed %d requests to download' +
                              ' item templates.', req_count)
                    time.sleep(random.uniform(.53, 1.1))
            except Exception as e:
                log.exception('Login for account %s failed. Exception in ' +
                              'downloading Item Templates: %s.',
                              account['username'], repr(e))
                raise LoginSequenceFail('Failed during login sequence.')

    try:  # 6 - Get Player Profile request.
        request = api.create_request()
        request.get_player_profile()
        request.check_challenge()
        request.get_hatched_eggs()
        request.get_inventory(last_timestamp_ms=account['last_timestamp_ms'])
        request.check_awarded_badges()
        request.download_settings(hash=account['remote_config']['hash'])
        request.get_buddy_walked()
        response = request.call()
        parse_get_inventory(account, response)
        time.sleep(random.uniform(.2, .3))
    except Exception as e:
        log.exception('Login for account %s failed. Exception in ' +
                      'get_player_profile: %s', account['username'], repr(e))
        raise LoginSequenceFail('Failed during login sequence.')

    try:  # 7 - Check if there are level up rewards to claim.
        request = api.create_request()
        request.level_up_rewards()
        request.check_challenge()
        request.get_hatched_eggs()
        request.get_inventory(last_timestamp_ms=account['last_timestamp_ms'])
        request.check_awarded_badges()
        request.download_settings(hash=account['remote_config']['hash'])
        request.get_buddy_walked()
        request.get_inbox(is_history=True,
                          is_reverse=False,
                          not_before_ms=0)
        response = request.call()
        parse_get_inventory(account, response)
        time.sleep(random.uniform(.45, .7))

    except Exception as e:
        log.exception('Login for account %s failed. Exception in ' +
                      'level_up_rewards: %s', account['username'], repr(e))
        raise LoginSequenceFail('Failed during login sequence.')

    # TODO: # 9 - Make a request to get Shop items.

    log.debug('Login for account %s successful.', account['username'])
    time.sleep(random.uniform(10, 20))


# Check if all important tutorial steps have been completed.
# API argument needs to be a logged in API instance.
def get_tutorial_state(response):
    responses = response.get('responses', {})
    get_player = responses.get('GET_PLAYER', {})
    tutorial_state = get_player.get(
        'player_data', {}).get('tutorial_state', [])
    return tutorial_state


# Complete minimal tutorial steps.
# API argument needs to be a logged in API instance.
# TODO: Check if game client bundles these requests, or does them separately.
def complete_tutorial(api, account, tutorial_state):
    if 0 not in tutorial_state:
        time.sleep(random.uniform(1, 5))
        request = api.create_request()
        request.mark_tutorial_complete(tutorials_completed=0)
        log.debug('Sending 0 tutorials_completed for %s.', account['username'])
        request.call()

    if 1 not in tutorial_state:
        time.sleep(random.uniform(5, 12))
        request = api.create_request()
        request.set_avatar(player_avatar={
            'hair': random.randint(1, 5),
            'shirt': random.randint(1, 3),
            'pants': random.randint(1, 2),
            'shoes': random.randint(1, 6),
            'avatar': random.randint(0, 1),
            'eyes': random.randint(1, 4),
            'backpack': random.randint(1, 5)
        })
        log.debug('Sending set random player character request for %s.',
                  account['username'])
        request.call()

        time.sleep(random.uniform(0.3, 0.5))

        request = api.create_request()
        request.mark_tutorial_complete(tutorials_completed=1)
        log.debug('Sending 1 tutorials_completed for %s.', account['username'])
        request.call()

    time.sleep(random.uniform(0.5, 0.6))
    request = api.create_request()
    request.get_player_profile()
    log.debug('Fetching player profile for %s...', account['username'])
    request.call()

    starter_id = None
    if 3 not in tutorial_state:
        time.sleep(random.uniform(1, 1.5))
        request = api.create_request()
        request.get_download_urls(asset_id=[
            '1a3c2816-65fa-4b97-90eb-0b301c064b7a/1477084786906000',
            'aa8f7687-a022-4773-b900-3a8c170e9aea/1477084794890000',
            'e89109b0-9a54-40fe-8431-12f7826c8194/1477084802881000'])
        log.debug('Grabbing some game assets.')
        request.call()

        time.sleep(random.uniform(1, 1.6))
        request = api.create_request()
        request.call()

        time.sleep(random.uniform(6, 13))
        request = api.create_request()
        starter = random.choice((1, 4, 7))
        request.encounter_tutorial_complete(pokemon_id=starter)
        log.debug('Catching the starter for %s.', account['username'])
        request.call()

        time.sleep(random.uniform(0.5, 0.6))
        request = api.create_request()
        request.get_player(
            player_locale={
                'country': 'US',
                'language': 'en',
                'timezone': 'America/Denver'})
        responses = request.call().get('responses', {})

        inventory = responses.get('GET_INVENTORY', {}).get(
            'inventory_delta', {}).get('inventory_items', [])
        for item in inventory:
            pokemon = item.get('inventory_item_data', {}).get('pokemon_data')
            if pokemon:
                starter_id = pokemon.get('id')

    if 4 not in tutorial_state:
        time.sleep(random.uniform(5, 12))
        request = api.create_request()
        request.claim_codename(codename=account['username'])
        log.debug('Claiming codename for %s.', account['username'])
        request.call()

        time.sleep(random.uniform(1, 1.3))
        request = api.create_request()
        request.mark_tutorial_complete(tutorials_completed=4)
        log.debug('Sending 4 tutorials_completed for %s.', account['username'])
        request.call()

        time.sleep(0.1)
        request = api.create_request()
        request.get_player(
            player_locale={
                'country': 'US',
                'language': 'en',
                'timezone': 'America/Denver'})
        request.call()

    if 7 not in tutorial_state:
        time.sleep(random.uniform(4, 10))
        request = api.create_request()
        request.mark_tutorial_complete(tutorials_completed=7)
        log.debug('Sending 7 tutorials_completed for %s.', account['username'])
        request.call()

    if starter_id:
        time.sleep(random.uniform(3, 5))
        request = api.create_request()
        request.set_buddy_pokemon(pokemon_id=starter_id)
        log.debug('Setting buddy pokemon for %s.', account['username'])
        request.call()
        time.sleep(random.uniform(0.8, 1.8))

    # Sleeping before we start scanning to avoid Niantic throttling.
    log.debug('And %s is done. Wait for a second, to avoid throttle.',
              account['username'])
    time.sleep(random.uniform(2, 4))
    return True


# Complete tutorial with a level up by a Pokestop spin.
# API argument needs to be a logged in API instance.
# Called during fort parsing in models.py
def tutorial_pokestop_spin(api, account, forts, step_location):
    if account['level'] > 1:
        log.debug(
            'No need to spin a Pokestop. ' +
            'Account %s is already level %d.',
            account['username'], account['level'])
    else:  # Account needs to spin a Pokestop for level 2.
        log.debug(
            'Spinning Pokestop for account %s.',
            account['username'])
        for fort in forts:
            if fort.get('type') == 1:
                if spin_pokestop(api, account, fort, step_location):
                    log.debug(
                        'Account %s successfully spun a Pokestop ' +
                        'after completed tutorial.',
                        account['username'])
                    return True

    return False


def get_player_level(api_response):
    inventory_items = api_response['responses'].get(
        'GET_INVENTORY', {}).get(
        'inventory_delta', {}).get(
        'inventory_items', [])
    player_stats = [item['inventory_item_data']['player_stats']
                    for item in inventory_items
                    if 'player_stats' in item.get(
                    'inventory_item_data', {})]
    if len(player_stats) > 0:
        player_level = player_stats[0].get('level', 1)
        return player_level

    return 0


def spin_pokestop(api, account, fort, step_location):
    spinning_radius = 0.04
    if in_radius((fort['latitude'], fort['longitude']), step_location,
                 spinning_radius):
        log.debug('Attempt to spin Pokestop (ID %s)', fort['id'])
        time.sleep(random.uniform(0.8, 1.8))  # Do not let Niantic throttle
        response = spin_pokestop_request(api, account, fort, step_location)
        time.sleep(random.uniform(2, 4))  # Do not let Niantic throttle

        # Check for reCaptcha
        captcha_url = response['responses'][
            'CHECK_CHALLENGE']['challenge_url']
        if len(captcha_url) > 1:
            log.debug('Account encountered a reCaptcha.')
            return False

        spin_result = response['responses']['FORT_SEARCH']['result']
        if spin_result is 1:
            log.debug('Successful Pokestop spin.')
            return True
        elif spin_result is 2:
            log.debug('Pokestop was not in range to spin.')
        elif spin_result is 3:
            log.debug('Failed to spin Pokestop. Has recently been spun.')
        elif spin_result is 4:
            log.debug('Failed to spin Pokestop. Inventory is full.')
        elif spin_result is 5:
            log.debug('Maximum number of Pokestops spun for this day.')
        else:
            log.debug(
                'Failed to spin a Pokestop. Unknown result %d.',
                spin_result)

    return False


def spin_pokestop_request(api, account, fort, step_location):
    try:
        req = api.create_request()
        req.fort_search(
            fort_id=fort['id'],
            fort_latitude=fort['latitude'],
            fort_longitude=fort['longitude'],
            player_latitude=step_location[0],
            player_longitude=step_location[1])
        req.check_challenge()
        req.get_hatched_eggs()
        req.get_inventory(last_timestamp_ms=account['last_timestamp_ms'])
        req.check_awarded_badges()
        req.get_buddy_walked()
        req.get_inbox(is_history=True,
                      is_reverse=False,
                      not_before_ms=0)
        response = req.call()
        parse_get_inventory(account, response)

        return response

    except Exception as e:
        log.error('Exception while spinning Pokestop: %s.', repr(e))
        return False


def encounter_pokemon_request(api, account, encounter_id, spawnpoint_id,
                              scan_location):
    try:
        # Setup encounter request envelope.
        req = api.create_request()
        req.encounter(
            encounter_id=encounter_id,
            spawn_point_id=spawnpoint_id,
            player_latitude=scan_location[0],
            player_longitude=scan_location[1])
        req.check_challenge()
        req.get_hatched_eggs()
        req.get_inventory(last_timestamp_ms=account['last_timestamp_ms'])
        req.check_awarded_badges()
        req.get_buddy_walked()
        req.get_inbox(is_history=True,
                      is_reverse=False,
                      not_before_ms=0)
        response = req.call()
        parse_get_inventory(account, response)

        return response
    except Exception as e:
        log.error('Exception while encountering Pokémon: %s.', repr(e))
        return False


def parse_download_settings(account, api_response):
    if 'DOWNLOAD_REMOTE_CONFIG_VERSION' in api_response['responses']:
        remote_config = (api_response['responses']
                         .get('DOWNLOAD_REMOTE_CONFIG_VERSION', 0))
        if 'asset_digest_timestamp_ms' in remote_config:
            asset_time = remote_config['asset_digest_timestamp_ms'] / 1000000
        if 'item_templates_timestamp_ms' in remote_config:
            template_time = remote_config['item_templates_timestamp_ms'] / 1000

        download_settings = {}
        download_settings['hash'] = api_response[
            'responses']['DOWNLOAD_SETTINGS']['hash']
        download_settings['asset_time'] = asset_time
        download_settings['template_time'] = template_time

        account['remote_config'] = download_settings

        log.debug('Download settings for account %s: %s',
                  account['username'], download_settings)
        return True


# Perform parsing for account information from the GET_INVENTORY response.
def parse_get_inventory(account, api_response):
    parse_new_timestamp_ms(account, api_response)
    parse_player_level(account, api_response)


# Parse new timestamp from the GET_INVENTORY response.
def parse_new_timestamp_ms(account, api_response):
    if 'GET_INVENTORY' in api_response['responses']:
        account['last_timestamp_ms'] = (api_response['responses']
                                                    ['GET_INVENTORY']
                                                    ['inventory_delta']
                                        .get('new_timestamp_ms', 0))


# Parse player level from the GET_INVENTORY response.
def parse_player_level(account, api_response):
    player_level = get_player_level(api_response)
    if player_level > account['level']:
        account['level'] = player_level
