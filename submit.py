import runhandler
import util
import logging
import runs
import json
import re

from operator import itemgetter
from datetime import date
from google.appengine.ext import db

GAME_CATEGORY_RE = re.compile( r"^[a-zA-Z0-9 +=.:!@#$%&*()'/\\-]{1,100}$" )
def valid_game_or_category( game_or_category ):
    return GAME_CATEGORY_RE.match( game_or_category )

class Submit( runhandler.RunHandler ):
    def get( self ):
        user = self.get_user( )
        if not user:
            self.redirect( "/" )
            return

        params = dict( user=user )

        # Are we editing an existing run?
        run_id = self.request.get( 'edit' )
        if run_id:
            # Grab the run to edit
            run = self.get_run_by_id( run_id )
            if not run or user.username != run.username:
                self.error( 404 )
                self.render( "404.html", user=user )
                return
            params[ 'game' ] = run.game
            params[ 'category' ] = run.category
            params[ 'time' ] = util.seconds_to_timestr( run.seconds )
            params[ 'date' ] = run.date
            params[ 'run_id' ] = run_id
            if run.video is not None:
                params[ 'video' ] = run.video
            if run.version is not None:
                params[ 'version' ] = run.version
        else:
            # Start with the game, category and version from this user's 
            # last run, as well as the current day
            run = self.get_last_run( user.username )
            params['date'] = date.today( )
            if run is not None:
                params['game'] = run.game
                params['category'] = run.category
                if run.version is not None:
                    params['version'] = run.version

        # Grab all of the games and categories for autocompleting
        params['categories'] = self.get_categories( )
                    
        self.render( "submit.html", **params )

    def post( self ):
        user = self.get_user( )
        if not user:
            self.redirect( "/" )
            return

        game = self.request.get( 'game' )
        category = self.request.get( 'category' )
        time = self.request.get( 'time' )
        datestr = self.request.get( 'date' )
        video = self.request.get( 'video' )
        version = self.request.get( 'version' )
        is_bkt = self.request.get( 'bkt', default_value="no" )
        if is_bkt == "yes":
            is_bkt = True
        else:
            is_bkt = False
        run_id = self.request.get( 'edit' )

        params = dict( user = user, game = game, category = category, 
                       time = time, video = video, 
                       version = version, run_id = run_id, is_bkt = is_bkt )

        valid = True

        # Make sure the game doesn't already exist under a similar name
        game_code = util.get_code( game )
        game_model = self.get_game_model( game_code )
        if not game_code:
            params['game_error'] = "Game cannot be blank"
            valid = False
        elif game_model is not None and game != game_model.game:
            params['game_error'] = ( "Game already exists under [" 
                                     + game_model.game + "] (case sensitive)."
                                     + " Hit submit again to confirm." )
            params['game'] = game_model.game
            valid = False
        elif not valid_game_or_category( game ):
            params['game_error'] = ( "Game name must not use any 'funny'"
                                     + " characters and can be up to 100 "
                                     + "characters long" )
            valid = False
        params[ 'game_code' ] = game_code
        params[ 'game_model' ] = game_model

        # Make sure the category doesn't already exist under a similar name
        category_code = util.get_code( category )
        category_found = False
        if not category_code:
            params['category_error'] = "Category cannot be blank"
            valid = False
        elif game_model is not None:
            infolist = json.loads( game_model.info )
            for info in infolist:
                if category_code == util.get_code( info['category'] ):
                    category_found = True
                    if category != info['category']:
                        params['category_error'] = ( "Category already exists "
                                                     + "under [" 
                                                     + info['category'] + "] "
                                                     + "(case sensitive). "
                                                     + "Hit submit again to "
                                                     + "confirm." )
                        params['category'] = info['category']
                        valid = False
                    break
        if not category_found and not valid_game_or_category( category ):
            params['category_error'] = ( "Category must not use any 'funny'"
                                         + " characters and can be up to 100 "
                                         + "characters long" )
            valid = False
        params[ 'category_found' ] = category_found

        # Parse the time into seconds, ensure it is valid
        ( seconds, time_error ) = util.timestr_to_seconds( time )
        if not seconds:
            params['time_error'] = "Invalid time: " + time_error
            params['seconds'] = -1
            valid = False
        else:
            time = util.seconds_to_timestr( seconds ) # Enforce standard form
            params[ 'time' ] = time
            params[ 'seconds' ] = seconds

        # Parse the date, ensure it is valid
        parts = datestr.split( '/' )
        if len( datestr ) <= 0:
            params[ 'date' ] = None
        elif len( parts ) != 3:
            params['date_error'] = "Bad date format: should be mm/dd/yyyy"
            params['date'] = date.today( )
            valid = False
        else:
            # strftime breaks with dates before 1900, but JayFermont suggested
            # they break before 1970, so let's disallow anything before 1970.
            # To help users out, let's change two-digit dates to the 1900/2000
            # equivalent.
            year = int( parts[ 2 ] )
            if year >= 0 and year <= 69:
                year += 2000
            elif year >= 70 and year < 100:
                year += 1900
            try:
                params['date'] = date( year, int( parts[ 0 ] ), 
                                       int( parts[ 1 ] ) )
                if params['date'] > date.today( ):
                    params['date_error'] = "That date is in the future!"
                    valid = False
                elif year < 1970:
                    params['date_error'] = "Date must be after Dec 31 1969"
                    valid = False
            except ValueError:
                params['date_error'] = "Invalid date"
                params['date'] = date.today( )
                valid = False
                
        # Check that if this is a best known time, that it beats the old
        # best known time
        if is_bkt and game_model is not None:
            gameinfolist = json.loads( game_model.info )
            for gameinfo in gameinfolist:
                if gameinfo['category'] == params['category']:
                    if( gameinfo.get( 'bk_seconds' ) is not None
                        and gameinfo['bk_seconds'] <= seconds ):
                        s = ( "This time does not beat current best known "
                              + "time of " + util.seconds_to_timestr( 
                                  gameinfo.get( 'bk_seconds' ) ) 
                              + " by " + gameinfo['bk_runner'] 
                              + " (if best known time is incorrect, you can "
                              + "update best known time after submission)" )
                        params['bkt_error'] = s
                        params['is_bkt'] = False
                        valid = False
                    break

        params['valid'] = valid
        
        if run_id:
            self.put_existing_run( params )
        else:
            self.put_new_run( params )


    def put_new_run( self, params ):
        user = params[ 'user' ]
        game = params[ 'game' ]
        category = params[ 'category' ]
        seconds = params[ 'seconds' ]
        time = params[ 'time' ]
        video = params[ 'video' ]
        version = params[ 'version' ]
        valid = params[ 'valid' ]

        # Add a new run to the database
        try:
            new_run = runs.Runs( username = user.username,
                                 game = game,
                                 category = category,
                                 seconds = seconds,
                                 date = params[ 'date' ],
                                 version = version,
                                 parent = runs.key() )
            try:
                if video:
                    new_run.video = video
            except db.BadValueError:
                params[ 'video_error' ] = "Invalid video URL"
                valid = False
        except db.BadValueError:
            valid = False
        
        if not valid:
            # Grab all of the games for autocompleting
            params['categories'] = self.get_categories( )

            self.render( "submit.html", **params )
            return

        new_run.put( )
        params[ 'run_id' ] = str( new_run.key().id() )
        logging.debug( "Put new run for runner " + user.username
                       + ", game = " + game + ", category = " + category 
                       + ", time = " + time )

        # Check whether this is the first run for this username, game,
        # category combination.  This will determine whether we need to update
        # the gamelist and runnerlist, as well as update the num_pbs
        # for the game.
        num_pbs_delta = 0
        num_runs = self.num_runs( user.username, game, category, 2 )
        if num_runs == 1:
            num_pbs_delta = 1

        # Update games.Games
        self.update_games_put( params, num_pbs_delta )

        # Update memcache
        self.update_cache_run_by_id( new_run.key().id(), new_run )
        # Must update runinfo before updating pblist, gamepage since these 
        # both rely on runinfo being up to date
        self.update_runinfo_put( params )
        self.update_pblist_put( params )
        self.update_gamepage_put( params )
        self.update_runlist_for_runner_put( params )
        self.update_cache_user_has_run( user.username, game, True )
        self.update_cache_last_run( user.username, new_run )
                     
        if num_runs <= 0:
            logging.error( "Unexpected count [" + str(count) 
                           + "] for number of runs for "
                           + username + ", " + game + ", " + category )
            self.update_cache_gamelist( None )
            self.update_cache_runnerlist( None )
        if num_pbs_delta == 1:
            self.update_gamelist_put( params )
            self.update_runnerlist_put( params )

        self.redirect( "/runner/" + util.get_code( user.username ) )


    def put_existing_run( self, params ):
        user = params[ 'user' ]
        game = params[ 'game' ]
        game_code = params[ 'game_code' ]
        category = params[ 'category' ]
        seconds = params[ 'seconds' ]
        time = params[ 'time' ]
        video = params[ 'video' ]
        version = params[ 'version' ]
        valid = params[ 'valid' ]
        run_id = params[ 'run_id' ]

        # Grab the old run, which we will update to be the new run
        new_run = self.get_run_by_id( run_id )
        if not new_run or new_run.username != user.username:
            self.error( 404 )
            self.render( "404.html", user=user )
            return

        # Store the contents of the old run
        old_run = dict( game = new_run.game,
                        category = new_run.category,
                        seconds = new_run.seconds )

        # Update the run
        try:
            new_run.game = game
            new_run.category = category
            new_run.seconds = seconds
            new_run.date = params['date']
            new_run.version = version
        except db.BadValueError:
            valid = False
        if video:
            try:
                new_run.video = video
            except db.BadValueError:
                params['video_error'] = "Invalid video URL"
                valid = False
        elif new_run.video:
            new_run.video = None
            
        if not valid:
            # Grab all of the games for autocompleting
            params['categories'] = self.get_categories( )

            self.render( "submit.html", **params )
            return
            
        new_run.put( )
        logging.debug( "Put updated run for runner " + user.username
                       + ", game = " + game + ", category = " + category
                       + ", time= " + time + ", run_id = " + run_id )

        # Figure out the change in num_pbs for the old and new game
        delta_num_pbs_old = 0
        delta_num_pbs_new = 0
        if game != old_run['game'] or category != old_run['category']:
            num_runs = self.num_runs( user.username, old_run[ 'game' ], 
                                      old_run[ 'category' ], 1 )
            if num_runs == 0:
                delta_num_pbs_old = -1
            num_runs = self.num_runs( user.username, game, category, 2 )
            if num_runs == 1:
                delta_num_pbs_new = 1
            
        # Update games.Games
        self.update_games_delete( old_run, delta_num_pbs_old )
        self.update_games_put( params, delta_num_pbs_new )

        # Update memcache with the removal of the old run and addition of the
        # new run.
        self.update_cache_run_by_id( run_id, new_run )
        # Must update runinfo before pblist and gamepage as in put_new_run()
        self.update_runinfo_delete( user, old_run )
        self.update_runinfo_put( params )
        self.update_pblist_delete( user, old_run )
        self.update_pblist_put( params )
        self.update_gamepage_delete( user, old_run )
        self.update_gamepage_put( params )
        self.update_user_has_run_delete( user, old_run )
        self.update_cache_user_has_run( user.username, game, True )

        # Update gamelist and runnerlist in memcache
        if delta_num_pbs_old == -1:
            self.update_gamelist_delete( old_run )
            self.update_runnerlist_delete( user )
        if delta_num_pbs_new == 1:
            self.update_gamelist_put( params )
            self.update_runnerlist_put( params )

        # Replace the old run in the runlist for runner in memcache
        runlist = self.get_runlist_for_runner( user.username, no_refresh=True )
        if runlist:
            for run in runlist:
                if run[ 'run_id' ] == run_id:
                    run[ 'game' ] = game
                    run[ 'game_code' ] = game_code
                    run[ 'category' ] = category
                    run[ 'time' ] = time
                    run[ 'date' ] = new_run.date
                    run[ 'video' ] = video
                    run[ 'version' ] = version
                    runlist.sort( key=lambda x: util.get_valid_date( 
                        x['date'] ), reverse=True )
                    self.update_cache_runlist_for_runner( user.username, 
                                                          runlist )
                    break

        # Check to see if we need to replace the last run for this user
        last_run = self.get_last_run( user.username, no_refresh=True )
        if( last_run is not None 
            and new_run.key().id() == last_run.key().id() ):
            self.update_cache_last_run( user.username, new_run )

        self.redirect( "/runner/" + util.get_code( user.username )
                       + "?q=view-all" )
