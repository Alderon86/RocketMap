#!/usr/bin/python
# -*- coding: utf-8 -*-

import logging
import time
import random
from threading import Lock
from timeit import default_timer

from pgoapi import PGoApi
from pgoapi.exceptions import AuthException

from .fakePogoApi import FakePogoApi
from .utils import in_radius, generate_device_info, equi_rect_distance
from .proxy import get_new_proxy

log = logging.getLogger(__name__)


class TooManyLoginAttempts(Exception):
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

    # Logged in? Enough time left? Cool!
    if api._auth_provider and api._auth_provider._ticket_expire:
        remaining_time = api._auth_provider._ticket_expire / 1000 - time.time()
        if remaining_time > 60:
            log.debug(
                'Credentials remain valid for another %f seconds.',
                remaining_time)
            return

    # Try to login. Repeat a few times, but don't get stuck here.
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

    log.debug('Login for account %s successful.', account['username'])
    time.sleep(20)


# Check if all important tutorial steps have been completed.
# API argument needs to be a logged in API instance.
def get_tutorial_state(api, account):
    log.debug('Checking tutorial state for %s.', account['username'])
    request = api.create_request()
    request.get_player(
        player_locale={'country': 'US',
                       'language': 'en',
                       'timezone': 'America/Denver'})

    response = request.call().get('responses', {})

    get_player = response.get('GET_PLAYER', {})
    tutorial_state = get_player.get(
        'player_data', {}).get('tutorial_state', [])
    time.sleep(random.uniform(2, 4))
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
def tutorial_pokestop_spin(api, player_level, forts, step_location, account):
    if player_level > 1:
        log.debug(
            'No need to spin a Pokestop. ' +
            'Account %s is already level %d.',
            account['username'], player_level)
    else:  # Account needs to spin a Pokestop for level 2.
        log.debug(
            'Spinning Pokestop for account %s.',
            account['username'])
        for fort in forts:
            if fort.get('type') == 1:
                if spin_pokestop(api, fort, step_location):
                    log.debug(
                        'Account %s successfully spun a Pokestop ' +
                        'after completed tutorial.',
                        account['username'])
                    return True

    return False


def get_player_level(map_dict):
    inventory_items = map_dict['responses'].get(
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


def spin_pokestop(api, fort, step_location):
    spinning_radius = 0.04
    if in_radius((fort['latitude'], fort['longitude']), step_location,
                 spinning_radius):
        log.debug('Attempt to spin Pokestop (ID %s)', fort['id'])

        time.sleep(random.uniform(0.8, 1.8))  # Do not let Niantic throttle
        spin_response = spin_pokestop_request(api, fort, step_location)
        time.sleep(random.uniform(2, 4))  # Do not let Niantic throttle

        # Check for reCaptcha
        captcha_url = spin_response['responses'][
            'CHECK_CHALLENGE']['challenge_url']
        if len(captcha_url) > 1:
            log.debug('Account encountered a reCaptcha.')
            return False

        spin_result = spin_response['responses']['FORT_SEARCH']['result']
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


def spin_pokestop_request(api, fort, step_location):
    try:
        req = api.create_request()
        spin_pokestop_response = req.fort_search(
            fort_id=fort['id'],
            fort_latitude=fort['latitude'],
            fort_longitude=fort['longitude'],
            player_latitude=step_location[0],
            player_longitude=step_location[1])
        req.check_challenge()
        req.get_hatched_eggs()
        req.get_inventory()
        req.check_awarded_badges()
        req.get_buddy_walked()
        spin_pokestop_response = req.call()

        return spin_pokestop_response

    except Exception as e:
        log.error('Exception while spinning Pokestop: %s.', repr(e))
        return False


def encounter_pokemon_request(api, encounter_id, spawnpoint_id, scan_location):
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
        req.get_inventory()
        req.check_awarded_badges()
        req.get_buddy_walked()
        encounter_result = req.call()

        return encounter_result
    except Exception as e:
        log.error('Exception while encountering Pokémon: %s.', repr(e))
        return False


# The AccountSet returns a scheduler that cycles through different
# sets of accounts (e.g. L30). Each set is defined at runtime, and is
# (currently) used to separate regular accounts from L30 accounts.
# TODO: Migrate the old account Queue to a real AccountScheduler, preferably
# handled globally via database instead of per instance.
# TODO: Accounts in the AccountSet are exempt from things like the
# account recycler thread. We could've hardcoded support into it, but that
# would have added to the amount of ugly code. Instead, we keep it as is
# until we have a proper account manager.
class AccountSet(object):

    def __init__(self, kph):
        self.sets = {}

        # Scanning limits.
        self.kph = kph

        # Thread safety.
        self.next_lock = Lock()

    # Set manipulation.
    def create_set(self, name, values=[]):
        if name in self.sets:
            raise Exception('Account set ' + name + ' is being created twice.')

        self.sets[name] = values

    # Release an account back to the pool after it was used.
    def release(self, account):
        if 'in_use' not in account:
            log.error('Released account %s back to the AccountSet,'
                      + " but it wasn't locked.",
                      account['username'])
        else:
            account['in_use'] = False

    # Get next account that is ready to be used for scanning.
    def next(self, set_name, coords_to_scan):
        # Yay for thread safety.
        with self.next_lock:
            # Readability.
            account_set = self.sets[set_name]

            # Loop all accounts for a good one.
            now = default_timer()
            max_speed_kmph = self.kph

            for i in range(len(account_set)):
                account = account_set[i]

                # Make sure it's not in use.
                if account.get('in_use', False):
                    continue

                # Make sure it's not captcha'd.
                if account.get('captcha', False):
                    continue

                # Check if we're below speed limit for account.
                last_scanned = account.get('last_scanned', False)

                if last_scanned:
                    seconds_passed = now - last_scanned
                    old_coords = account.get('last_coords', coords_to_scan)

                    distance_km = equi_rect_distance(
                        old_coords,
                        coords_to_scan)
                    cooldown_time_sec = distance_km / max_speed_kmph * 3600

                    # Not enough time has passed for this one.
                    if seconds_passed < cooldown_time_sec:
                        continue

                # We've found an account that's ready.
                account['last_scanned'] = now
                account['last_coords'] = coords_to_scan
                account['in_use'] = True

                return account

        # TODO: Instead of returning False, return the amount of min. seconds
        # the instance needs to wait until the first account becomes available,
        # so it doesn't need to keep asking if we know we need to wait.
        return False


# Check if Pokestop is spinnable and not on cooldown.
def pokestop_spinnable(fort, step_location):
    spinning_radius = 0.038  # Maximum distance to spin Pokestops.
    in_range = in_radius((fort['latitude'], fort['longitude']), step_location,
                         spinning_radius)
    now = time.time()
    pause_needed = 'cooldown_complete_timestamp_ms' in fort and fort[
        'cooldown_complete_timestamp_ms'] / 1000 > now
    return in_range and not pause_needed


# 50% Chance to spin a Pokestop.
def spinning_try(api, fort, step_location, account, map_dict, args):
    if account['hour_spins'] > args.account_max_spins:
        log.info('Account %s has reached its Pokestop spinning limits.',
                 account['username'])
        return False

    # Set 50% Chance to spin a Pokestop.
    if random.randint(0, 100) < 50:
        time.sleep(random.uniform(2, 4))  # Do not let Niantic throttle.
        spin_response = spin_pokestop_request(api, fort, step_location)
        if not spin_response:
            return False

        # Check for reCaptcha
        captcha_url = spin_response['responses']['CHECK_CHALLENGE'][
            'challenge_url']
        if len(captcha_url) > 1:
            log.debug('Account encountered a reCaptcha.')
            return False

        # Catch all possible responses.
        spin_result = spin_response['responses']['FORT_SEARCH']['result']
        if spin_result is 1:
            log.info('Successful Pokestop spin with %s.', account['username'])
            # Update account stats and clear inventory if necessary.
            parse_inventory(api, account, map_dict)
            clear_inventory(api, account)
            account['session_spins'] += 1
            account['used_pokestops'][fort['id']] = time.time()
            incubate_eggs(api, account)
            return True
        # Catch all other results.
        elif spin_result is 2:
            log.info('Pokestop %s was not in range to spin for account %s',
                     fort['id'], account['username'])
        elif spin_result is 3:
            log.info('Failed to spin Pokestop %s. %s Has recently spun this' +
                     'stop.', fort['id'], account['username'])
        elif spin_result is 4:
            log.info('Failed to spin Pokestop %s. %s Inventory is full.',
                     fort['id'], account['username'])
            log.info('Clearing Inventory...')
            clear_inventory(api, account)
        elif spin_result is 5:
            log.info('Account %s has spun maximum Pokestops for today.',
                     account['username'])
        else:
            log.info('Failed to spin a Pokestop with account %s .' +
                     'Unknown result %d.', account['username'], spin_result)
    return False


# Parse player stats and inventory into account.
def parse_inventory(api, account, map_dict):
    inventory = map_dict['responses'][
        'GET_INVENTORY']['inventory_delta']['inventory_items']
    parsed_items = 0
    parsed_pokemons = 0
    parsed_eggs = 0
    for item in inventory:
        item_data = item.get('inventory_item_data', {})
        if 'player_stats' in item_data:
            stats = item_data['player_stats']
            account['level'] = stats['level']
            account['spins'] = stats.get('poke_stop_visits', 0)
            account['walked'] = stats.get('km_walked', 0)

            log.info('Parsed %s player stats: level %d, %f km ' +
                     'walked, %d spins.',
                     account['username'], account['level'], account['walked'],
                     account['spins'])
        elif 'item' in item_data:
            item_id = item_data['item']['item_id']
            item_count = item_data['item'].get('count', 0)
            account['items'][item_id] = item_count
            parsed_items += item_count
        elif 'egg_incubators' in item_data:
            incubators = item_data['egg_incubators']['egg_incubator']
            for incubator in incubators:
                account['incubators'][incubator['id']] = {
                    'item_id': incubator['item_id'],
                    'uses_remaining': incubator.get('uses_remaining', 0),
                    'pokemon_id': incubator.get('pokemon_id', 0)
                }
        if 'pokemon_data' in item_data:
            p_data = item_data['pokemon_data']
            p_id = p_data.get('id', 0)
            pokemon_id = p_data.get('pokemon_id', 0)
            is_egg = p_data.get('is_egg', False)
            if p_id and pokemon_id:
                account['pokemons'][p_id] = {
                    'pokemon_id': pokemon_id,
                    'move_1': p_data['move_1'],
                    'move_2': p_data['move_2'],
                    'height': p_data['height_m'],
                    'weight': p_data['weight_kg'],
                    'gender': p_data['pokemon_display']['gender'],
                    'cp': p_data['cp'],
                    'cp_multiplier': p_data['cp_multiplier']
                }
                parsed_pokemons += 1
            elif p_id and is_egg:
                if p_data.get('egg_incubator_id', None):
                    # Egg is already incubating.
                    continue
                account['eggs'][p_id] = {
                    'captured_cell_id': p_data['captured_cell_id'],
                    'creation_time_ms': p_data['creation_time_ms'],
                    'km_target': p_data['egg_km_walked_target']
                }
                parsed_eggs += 1
    log.info(
        'Parsed %s player inventory: %d items, %d pokemons and %d eggs.',
        account['username'], parsed_items, parsed_pokemons, parsed_eggs)


def reset_account(account):
    account['start_time'] = time.time()
    account['max_items'] = 350
    account['max_pokemons'] = 250
    account['items'] = {}
    account['pokemons'] = {}
    account['incubators'] = {}
    account['eggs'] = {}
    account['level'] = 0
    account['used_pokestops'] = {}
    account['spins'] = 0
    account['session_spins'] = 0
    account['hour_spins'] = 0
    account['walked'] = 0.0


def cleanup_account_stats(account, pokestop_timeout):
    elapsed_time = time.time() - account['start_time']

    # Just to prevent division by 0 errors, when needed
    # set elapsed to 1 millisecond
    if elapsed_time == 0:
        elapsed_time = 1

    spins_h = account['session_spins'] * 3600.0 / elapsed_time
    account['hour_spins'] = spins_h

    # Refresh visited pokestops that were on timeout.
    used_pokestops = dict(account['used_pokestops'])
    for pokestop_id in account['used_pokestops']:
        last_attempt = account['used_pokestops'][pokestop_id]
        if (last_attempt + pokestop_timeout) < time.time():
            del used_pokestops[pokestop_id]
    account['used_pokestops'] = used_pokestops


def clear_inventory_request(api, item_id, drop_count):
    try:
        req = api.create_request()
        req.recycle_inventory_item(item_id=item_id, count=drop_count)
        req.check_challenge()
        req.get_hatched_eggs()
        req.get_inventory()
        req.check_awarded_badges()
        req.get_buddy_walked()
        clear_inventory_response = req.call()

        return clear_inventory_response

    except Exception as e:
        log.warning('Exception while clearing Inventory: %s', repr(e))
        return False


def clear_inventory(api, account):
    item_ids = [1, 2, 3,
                101, 102, 103, 104, 201, 202,
                701, 703, 705, 1101,
                1102, 1103, 1104, 1105]

    item_names = ['Pokeball', 'Greatball', 'Ultraball',
                  'Potion', 'Super Potion', 'Hyper Potion', 'Max Potion',
                  'Revive', 'Max Revive',
                  'Razz Berry', 'Nanab Berry', 'Pinap Berry', 'Sun Stone',
                  'Kings Rock', 'Metal Coat', 'Dragon Scale', 'Upgrade']

    indexes = range(len(item_ids))
    clear_responses = []
    for i in indexes:
        item_count = account['items'].get(item_ids[i], 0)
        if item_count > random.randint(5, 10):
            item_id = item_ids[i]
            item_name = item_names[i]
            drop_count = item_count - 5

            # Do not let Niantic throttle
            time.sleep(random.uniform(2, 4))
            clear_inventory_response = clear_inventory_request(
                api, item_id, drop_count)

            captcha_url = clear_inventory_response['responses'][
                'CHECK_CHALLENGE']['challenge_url']
            if len(captcha_url) > 1:
                log.info('Account encountered a reCaptcha.')
                return False

            clear_response = clear_inventory_response[
                'responses']['RECYCLE_INVENTORY_ITEM']
            clear_responses.append(clear_response)

            clear_result = clear_response['result']
            if clear_result is 1:
                log.info('Clearing %s %ss succeeded.', item_count,
                         item_name)
            elif clear_result is 2:
                log.debug('Not enough items to clear, parsing failed.')
            elif clear_result is 3:
                log.debug('Tried to recycle incubator, parsing failed.')
            else:
                log.warning('Failed to clear inventory.')

            log.debug('Recycled inventory: \n\r{}'.format(clear_responses))

    return clear_responses


def incubate_eggs(api, account):
    incubators = dict(account['incubators'])
    for incubator_id, incubator in incubators.iteritems():
        egg_ids = account['eggs'].keys()
        if not egg_ids:
            log.debug('Account %s has no eggs to incubate.',
                      account['username'])
            break
        if incubator['pokemon_id'] == 0 and incubator['item_id'] == 901:
            egg_id = random.choice(egg_ids)
            km_target = account['eggs'][egg_id]['km_target']
            time.sleep(random.uniform(2.0, 4.0))
            request_use_item_egg_incubator(api, account, incubator_id,
                                           egg_id)
            log.info('Egg #%s ({:.1f} km) is on incubator #%s.').format(
                    egg_id, km_target, incubator_id)
            del account['eggs'][egg_id]
        elif incubator['pokemon_id'] != 0:
            log.info('Already incubating egg!')
            break
        else:
                log.error('Failed to put egg on incubator #%s.', incubator_id)
                return False

    return True


def request_use_item_egg_incubator(api, account, incubator_id, egg_id):
    try:
        req = api.create_request()
        req.use_item_egg_incubator(
            item_id=incubator_id,
            pokemon_id=egg_id
        )
        req.check_challenge()
        req.get_hatched_eggs()
        req.get_inventory()
        req.check_awarded_badges()
        req.get_buddy_walked()
        req.call()

    except Exception as e:
        log.warning('Exception while putting an egg in incubator: %s', repr(e))

    return False
