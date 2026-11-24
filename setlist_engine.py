"""
Setlist logic, adapted from the original find_doable_songs.py (Amitha's script).
The algorithm is unchanged; it's just separated from the Windows window code
so the web page (app.py) can call it.
"""
import io
import math
import random

import pandas
import requests

SHEET_ID = '1z1c632UCyPgrChQjbOFAwJdHVgKyJd1oi2c7-TGCJt0'


def sheet_url(tab):
    return f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={tab}'


def read_tab(tab):
    """Download one tab as CSV, with a time limit and one retry so the app
    never hangs forever if Google is slow to respond."""
    last_error = None
    for _ in range(2):
        try:
            response = requests.get(sheet_url(tab), timeout=20,
                                    headers={'User-Agent': 'Mozilla/5.0 (setlist-builder)'})
            response.raise_for_status()
            if 'text/csv' not in response.headers.get('Content-Type', ''):
                raise RuntimeError(f'Google returned a web page instead of the "{tab}" tab. Is the sheet still shared as "Anyone with the link"?')
            return pandas.read_csv(io.StringIO(response.content.decode('utf-8')))
        except Exception as err:
            last_error = err
    raise RuntimeError(f'Could not load the "{tab}" tab: {last_error}')


def load_sheet():
    """Read the three tabs from the group's Google Sheet."""
    df_songs = read_tab('Songs')
    df_members = read_tab('Members')
    df_banned = read_tab('Banned_Orderings')
    # The original code expects an 'Order' column before 'Song'. The sheet no
    # longer has one, so add an empty one to keep column positions the same.
    if df_songs.columns[0] != 'Order':
        df_songs.insert(0, 'Order', '')
    # Drop fully blank rows (e.g. trailing empty rows in the sheet)
    df_songs = df_songs.dropna(subset=['Song'])
    return df_songs, df_members, df_banned


def compute_breaks(num_songs_needed, num_breaks_needed):
    set_length = math.ceil(num_songs_needed / (num_breaks_needed + 1))
    remainder = num_songs_needed % (num_breaks_needed + 1)
    tracker = remainder
    break_list = []
    current_position = set_length
    for _ in range(num_breaks_needed):
        if remainder > 0:
            tracker -= 1
            if tracker < 0:
                current_position -= 1
        break_list.append(current_position)
        current_position += set_length
    return break_list


class SetlistError(Exception):
    """A problem with the spreadsheet data (typo in a name, etc.)."""


def build_setlist(df_set_list, df_members, df_banned_orderings, missing_people,
                  songs_to_remove, num_songs_needed, num_breaks_needed, holiday):
    """Returns dict with: ordered (DataFrame), unavailable (DataFrame), notes,
    available_songs, unavailable_songs, success."""
    Notes_list = []
    song_delete_list = [s.lower() for s in songs_to_remove]

    banned_ordering_lookup = {}
    for _, row in df_banned_orderings.iterrows():
        if not isinstance(row.get('Song1'), str) or not isinstance(row.get('Song2'), str):
            continue
        banned_ordering_lookup.setdefault(row['Song1'].lower(), []).append(row['Song2'].lower())

    members = sorted(df_members["Names"].dropna().values.tolist())
    break_list = compute_breaks(num_songs_needed, num_breaks_needed)

    def clean_name(name):
        return name.lower().strip()

    def clean_role_name(name):
        return name.lower().strip().replace(' ', '')

    all_name_set = set([w.lower() for w in members])
    missing_people = [clean_name(p) for p in missing_people if len(p) > 0]
    break_list = [b + 1 for b in break_list]

    var_names = df_set_list.columns.values.tolist()
    lol = df_set_list.values.tolist()

    class Song:
        def __init__(self, name="", song_type="Standard"):
            self.name = name
            self.person_dict = {}
            self.role_dict = {}
            self.alternate_role_dict = {}
            self.empty_roles = []
            self.note_list = []
            self.eligible_next_songs = []
            self.song_type = song_type
            self.num_replacements = 0
            self.can_precede_break = False
            self.used = 'unknown'

        def get_role(self, role_name):
            if role_name in self.role_dict and self.role_dict[role_name].person_list:
                return self.person_dict[self.role_dict[role_name].person_list[0].name]
            return None

    def remove_element(lst, element):
        return [x for x in lst if x != element]

    class Role:
        def __init__(self, name=""):
            self.name = name
            self.person_list = []
            self.alternate_person_list = []
            self.missing_person_list = []
            self.song = None
            self.alternate_role = None

        def remove_alternate(self, person):
            self.alternate_person_list = remove_element(self.alternate_person_list, person)
            self.alternate_role.person_list = remove_element(self.alternate_role.person_list, person)
            person.role_list = remove_element(person.role_list, self.alternate_role)

    class Person:
        def __init__(self, name=""):
            self.name = name
            self.role_list = []
            self.song = None

    # Recursively find a replacement for a role (and for the replacer's role, etc.)
    def find_replacement(role, song):
        for person in role.alternate_person_list:
            previous_role_list = person.role_list
            if len(previous_role_list) > 1:
                for role_temp in previous_role_list:
                    if 'solo' not in role_temp.name.lower():
                        raise SetlistError('Multiple previous roles for a replacement person. This should not happen.')
            previous_role = previous_role_list[0]
            previous_role_num = len(previous_role.person_list)
            solo = 1 if ('solo' in role.name.lower() and 'solo' in previous_role.name.lower()) else 0
            if previous_role_num > 1 or solo:
                role.remove_alternate(person)
                return person, previous_role, solo
            else:
                if len(previous_role.alternate_person_list) == 0:
                    continue
                replacement_person, previous_previous_role, previous_solo = find_replacement(previous_role, song)
                if replacement_person is not None:
                    note = song.name + ' note: replacing ' + person.name + ' with ' + replacement_person.name + ' for ' + previous_role.name
                    song.num_replacements += 1
                    song.note_list.append(note)
                    Notes_list.append(note)
                    if previous_solo == 1:
                        replacement_person.role_list.append(previous_role)
                        previous_role.person_list.append(replacement_person)
                    else:
                        replacement_person.role_list = [previous_role]
                        previous_role.person_list.append(replacement_person)
                        previous_previous_role.person_list.remove(replacement_person)
                    return person, previous_role, 0
        return None, None, None

    good_song_names = []
    good_songs = []
    bad_songs = []
    output_list_good = []
    output_list_bad = []
    sa_tb_songs = []
    output_lookup_good = {}
    order = 0

    lol = [r for r in lol if isinstance(r[1], str) and r[1].lower() not in song_delete_list]
    num_retired_songs = len([r for r in lol if str(r[2]).lower() == 'retired'])

    for row in lol:
        song = Song(name=row[1], song_type=row[2])
        start_row_index = 3
        for i in range(start_row_index, len(var_names)):
            role_name = clean_role_name(var_names[i])
            val = row[i]
            if 'alternate' not in role_name:
                if val is not None and not isinstance(val, float):
                    role = Role(name=role_name)
                    role.song = song
                    song.role_dict[role_name] = role
                    for person_name in val.split(','):
                        person_name = clean_name(person_name)
                        if not person_name:
                            continue
                        if person_name not in all_name_set:
                            raise SetlistError(f'"{person_name}" is either a typo or not a current group member, but was listed as {role.name} for {song.name}. If they are in the group, add them to the Members tab of the spreadsheet.')
                        if person_name in song.person_dict:
                            raise SetlistError(f'{person_name} appears in both {role.name} and {song.person_dict[person_name].role_list[0].name} for {song.name}.')
                        person = Person(name=person_name)
                        person.song = song
                        person.role_list.append(role)
                        if person_name not in missing_people:
                            song.person_dict[person_name] = person
                            role.person_list.append(person)
                        else:
                            role.missing_person_list.append(person.name)
                else:
                    song.empty_roles.append(role_name)

        all_only = all_name_set - set(song.person_dict) - set(missing_people)
        if len(all_only) > 0:
            raise SetlistError(f'{song.name} does not include these group member(s): {", ".join(sorted(all_only))}. Every member needs a part (or "Resting") for every song. If they are no longer in the group, remove them from the Members tab.')

        for i in range(start_row_index, len(var_names)):
            role_name = clean_role_name(var_names[i])
            val = row[i]
            if 'alternate' in role_name:
                if val is not None and not isinstance(val, float):
                    role = Role(name=role_name)
                    role.song = song
                    song.alternate_role_dict[role_name] = role
                    for person_name in val.split(','):
                        person_name = clean_name(person_name)
                        if not person_name:
                            continue
                        if person_name not in missing_people:
                            if person_name not in song.person_dict:
                                raise SetlistError(f'"{person_name}" is listed as {role_name} for {song.name} but isn\'t found elsewhere in that song (typo?).')
                            person = song.person_dict[person_name]
                            role_name_orig = role_name.replace('alternate', '')
                            if role_name_orig not in song.role_dict:
                                continue  # alternate for a part nobody sings in this song
                            original_role = song.role_dict[role_name_orig]
                            original_role.alternate_person_list.append(person)
                            original_role.alternate_role = role
                            role.person_list.append(person)
                        else:
                            role.missing_person_list.append(person_name)
                else:
                    song.empty_roles.append(role_name)

        # Core processing: fill empty parts with alternates
        bad = 0
        for role_name in song.role_dict:
            role = song.role_dict[role_name]
            if len(role.person_list) == 0:
                replacement_person, previous_role, solo = find_replacement(role, song)
                if replacement_person is None:
                    note = song.name + ' failed because no replacement was found for ' + ', '.join(role.missing_person_list) + ' (' + role.name + ')'
                    Notes_list.append(note)
                    song.note_list.append(note)
                    bad = 1
                else:
                    note = song.name + ' note: replacing ' + ', '.join(role.missing_person_list) + ' with ' + replacement_person.name + ' for ' + role.name
                    Notes_list.append(note)
                    song.note_list.append(note)
                    if solo == 1:
                        replacement_person.role_list.append(role)
                        role.person_list.append(replacement_person)
                    else:
                        replacement_person.role_list = [role]
                        role.person_list.append(replacement_person)
                        previous_role.person_list.remove(replacement_person)

        if bad == 0:
            order += 1
        row_list = [str(order), song.name, song.song_type]
        for var_name in var_names:
            var_name_clean = clean_role_name(var_name)
            if var_name_clean in song.role_dict:
                role_names = ", ".join([p.name for p in song.role_dict[var_name_clean].person_list])
                row_list.append(role_names or 'NO REPLACEMENTS AVAILABLE')
            elif var_name_clean in song.alternate_role_dict:
                role_names = ", ".join([p.name for p in song.alternate_role_dict[var_name_clean].person_list])
                row_list.append(role_names or 'N/A')
            elif var_name_clean in song.empty_roles:
                row_list.append('N/A')
        row_list.append(". ".join(song.note_list))

        if bad == 1:
            bad_songs.append(song.name)
            output_list_bad.append(row_list)
        else:
            good_song_names.append(song.name)
            if song.song_type in ['tb', 'sa']:
                sa_tb_songs.append(song)
            else:
                good_songs.append(song)
            output_list_good.append(row_list)
            output_lookup_good[song.name] = row_list

    # Work out which songs can follow which
    prev_song_type = None
    for song1 in good_songs:
        ineligible_solo_list = [song1.get_role(p).name for p in ['solo1', 'solo2', 'vp'] if song1.get_role(p) is not None]
        ineligible_vp_list = [song1.get_role(p).name for p in ['vp'] if song1.get_role(p) is not None]
        for song2 in good_songs + sa_tb_songs:
            solo_list = [song2.get_role(p).name for p in ['solo1', 'solo2'] if song2.get_role(p) is not None]
            vp_list = [song2.get_role(p).name for p in ['vp'] if song2.get_role(p) is not None]
            problem = False
            if song2.name.lower() in banned_ordering_lookup.get(song1.name.lower(), []):
                problem = True
            if song1.name.lower() == "auld lang syne":
                problem = True
            if any(s in ineligible_solo_list for s in solo_list):
                problem = True
            if any(v in ineligible_vp_list for v in vp_list):
                problem = True
            if not problem:
                if song2.song_type == 'tb':
                    song1.can_precede_break = True
                elif song2.song_type != 'sa':
                    if holiday and prev_song_type == song1.song_type and prev_song_type == song2.song_type:
                        continue
                    song1.eligible_next_songs.append(song2)
        prev_song_type = song1.song_type

    remaining_song_dict = {s.name: s for s in good_songs}

    # Recursive search for an ordering that works
    def get_next_song(current_song, remaining_song_dict, num_leftover_songs, song_count, retired_songs):
        if current_song.song_type == 'retired':
            retired_songs -= 1
        if retired_songs < 0:
            return None
        first_sa_tb = False
        if song_count + 1 not in break_list:
            eligible_next_songs = current_song.eligible_next_songs
        else:
            eligible_next_songs = good_songs
            if len(sa_tb_songs) >= 2 and sa_tb_songs[0].used is not True:
                first_sa_tb = True
                num_leftover_songs += 2
                for song_temp in sa_tb_songs:
                    song_temp.used = True
        if retired_songs == 0:
            eligible_next_songs = [e for e in eligible_next_songs if str(e.song_type).lower() != 'retired']

        sortable = [[s, f'{s.song_type}_{s.num_replacements}_{random.uniform(0, 1)}'] for s in eligible_next_songs]
        sortable.sort(key=lambda x: x[1])
        for possible_song, _ in sortable:
            if possible_song.name in remaining_song_dict:
                temp_diff = len(remaining_song_dict) - 1 - num_leftover_songs
                if temp_diff <= 0:
                    next_return = [possible_song, current_song] if temp_diff == 0 else [current_song]
                    if first_sa_tb:
                        next_return += sa_tb_songs
                    return next_return
                next_song_dict = remaining_song_dict.copy()
                del next_song_dict[possible_song.name]
                next_return = get_next_song(possible_song, next_song_dict, num_leftover_songs, song_count + 1, retired_songs)
                if next_return is None:
                    continue
                next_return.append(current_song)
                if first_sa_tb:
                    next_return += sa_tb_songs
                return next_return
        return None

    set_list = None
    if len(good_songs) >= num_songs_needed:
        num_leftover_songs = len(good_songs) - num_songs_needed
        for song in good_songs:
            next_song_dict = remaining_song_dict.copy()
            del next_song_dict[song.name]
            for i in range(num_retired_songs + 1):
                set_list = get_next_song(song, next_song_dict, num_leftover_songs, 1, i)
                if set_list is not None:
                    break
            if set_list is not None:
                if i > 0:
                    Notes_list.append(f'Warning: used {i} retired song(s)!')
                break
        if set_list is None:
            Notes_list.append('Warning: songs cannot be put in order')
    else:
        Notes_list.append('Warning: not enough valid songs to make the desired set list')

    out_columns = var_names + ['Notes']
    index = 1
    if set_list is None:
        ordered_output_list = output_list_good
    else:
        set_list.reverse()
        ordered_output_list = []
        song_index = 1
        for song in set_list:
            row = output_lookup_good[song.name]
            row[0] = index
            ordered_output_list.append(row)
            index += 1
            song_index += 1
            if song_index in break_list and (len(sa_tb_songs) < 2 or song_index > break_list[0]):
                break_row = ['---'] * len(row)
                break_row[1] = '[break]'
                break_row[0] = index
                ordered_output_list.append(break_row)
                index += 1

    # Add back songs not in the set but still doable
    if len(good_songs) > num_songs_needed and ordered_output_list:
        break_row = ['---'] * len(ordered_output_list[0])
        break_row[1] = '[end of set]'
        break_row[0] = index
        ordered_output_list.append(break_row)
        index += 1
        used_set = {r[1] for r in ordered_output_list}
        for row in output_list_good:
            if row[1] not in used_set:
                row[0] = index
                ordered_output_list.append(row)
                index += 1

    return {
        'success': set_list is not None,
        'ordered': pandas.DataFrame(ordered_output_list, columns=out_columns),
        'unavailable': pandas.DataFrame(output_list_bad, columns=out_columns),
        'notes': Notes_list,
        'available_songs': good_song_names,
        'unavailable_songs': bad_songs,
    }
